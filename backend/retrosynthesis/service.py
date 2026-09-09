"""AiZynthFinder wrapped as a long-lived service.

The model + ZINC stock take ~8s to load and ~1GB of RAM, so one instance is
built at process startup and reused for every request.
"""
from __future__ import annotations

import base64
import io
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from aizynthfinder.aizynthfinder import AiZynthFinder
from rdkit import Chem

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.yml"

DEFAULT_ITERATION_LIMIT = 100
MAX_ITERATION_LIMIT = 500


class InvalidRequestError(ValueError):
    """Bad caller input (unparseable SMILES, out-of-range limit) -> HTTP 400."""


def load_config(config_path: Path, root: Path = ROOT) -> dict:
    """config.yml holds repo-relative paths; resolve them so CWD doesn't matter."""

    def fix(value: Any) -> Any:
        if isinstance(value, str) and not Path(value).is_absolute():
            return str(root / value)
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value

    return fix(yaml.safe_load(config_path.read_text()))


def _molecule_node(node: dict) -> dict:
    """Convert one AiZynthFinder tree node into the API's molecule shape."""
    return {
        "molecule_smiles": node["smiles"],
        "is_stock_available": node.get("in_stock", False),
        "reactions": [
            {
                "reactants": [_molecule_node(mol) for mol in rxn.get("children", [])],
                "template_used": rxn.get("metadata", {}).get("template_code"),
                "template_smarts": rxn.get("metadata", {}).get("template"),
                "score": rxn.get("metadata", {}).get("policy_probability"),
                "reaction_smiles": rxn.get("smiles", ""),
                "classification": rxn.get("metadata", {}).get("classification"),
            }
            for rxn in node.get("children", [])
        ],
    }


def _png_base64(image) -> Optional[str]:
    if image is None:
        return None
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class RetrosynthesisService:
    """Loads AiZynthFinder once; plans routes on demand."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG) -> None:
        if not config_path.exists():
            raise FileNotFoundError(
                f"{config_path} not found - run: download_public_data data/external/aizynthfinder"
            )
        self.config_path = config_path
        self.ready = False
        self.load_time_seconds = 0.0
        # ponytail: one finder behind a lock, so searches serialise. AiZynthFinder
        # keeps per-search state on the instance (target_mol, tree, routes), so it is
        # not safe to share across threads. Move to a worker pool of processes if
        # concurrent throughput ever matters more than the ~1GB per extra copy.
        self._lock = threading.Lock()

        started = time.time()
        self._finder = AiZynthFinder(configdict=load_config(config_path))
        self._finder.stock.select("zinc")
        self._finder.expansion_policy.select("uspto")
        self._finder.filter_policy.select("uspto")
        self.load_time_seconds = round(time.time() - started, 2)
        self.ready = True

    def plan_routes(
        self,
        smiles: str,
        top_n: int = 5,
        iteration_limit: int = DEFAULT_ITERATION_LIMIT,
        include_images: bool = False,
    ) -> dict:
        """Plan retrosynthetic routes for `smiles`.

        Only *solved* routes are returned - every leaf purchasable from the stock.
        An unsolved search returns `is_solved: false` and an empty `routes` list
        rather than the highest-scoring unsolved fragment, which is chemically
        unreliable even though it carries a confident-looking score.
        """
        if not isinstance(smiles, str) or not smiles.strip():
            raise InvalidRequestError("smiles must be a non-empty string")
        if Chem.MolFromSmiles(smiles) is None:
            raise InvalidRequestError(f"could not parse SMILES: {smiles!r}")
        if top_n < 1:
            raise InvalidRequestError("top_n must be >= 1")
        if not 1 <= iteration_limit <= MAX_ITERATION_LIMIT:
            raise InvalidRequestError(
                f"iteration_limit must be between 1 and {MAX_ITERATION_LIMIT}"
            )

        with self._lock:
            self._finder.config.search.iteration_limit = iteration_limit
            self._finder.target_smiles = smiles
            self._finder.tree_search()
            self._finder.build_routes()

            stats = self._finder.extract_statistics()
            self._finder.routes.dicts  # materialises route["dict"]

            solved = [
                i
                for i in range(len(self._finder.routes))
                if self._finder.routes[i]["reaction_tree"].is_solved
            ]

            routes = []
            for rank, idx in enumerate(solved[:top_n]):
                route = self._finder.routes[idx]
                tree = route["reaction_tree"]
                scores = {k: float(v) for k, v in self._finder.routes.scores[idx].items()}
                routes.append(
                    {
                        "route_id": rank,
                        "state_score": scores.get("state score"),
                        "scores": scores,
                        "number_of_reactions": len(list(tree.reactions())),
                        "tree": _molecule_node(route["dict"]),
                        # rendered per route, not via routes.images, which would draw
                        # all 25 candidates when the caller asked for top_n
                        "image_png_base64": (
                            _png_base64(tree.to_image()) if include_images else None
                        ),
                    }
                )

            return {
                "target_smiles": self._finder.target_smiles,
                "is_solved": bool(stats["is_solved"]),
                "search_time_seconds": round(float(stats["search_time"]), 2),
                "iterations_used": int(self._finder.search_stats["iterations"]),
                "iteration_limit": iteration_limit,
                "solved_routes_found": len(solved),
                "routes_returned": len(routes),
                "routes": routes,
            }
