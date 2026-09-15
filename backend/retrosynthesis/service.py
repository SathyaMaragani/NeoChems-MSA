"""AiZynthFinder wrapped as a long-lived service.

The model + ZINC stock take ~8s to load and ~1GB of RAM, so one instance is
built at process startup and reused for every request.
"""
from __future__ import annotations

import base64
import io
import logging
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

logger = logging.getLogger("ramchems.retrosynthesis")


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
    """Convert one AiZynthFinder tree node into the API's molecule shape.

    Two fields already available from AiZynthFinder are now surfaced:
    `template_hash` (stable across template files, unlike the numeric code) and
    `template_occurrence` - how many times the template appears in the USPTO
    template library. Occurrence is a LIBRARY COUNT: not a count of successful
    experiments, not a yield, not a probability. The field name and the UI label
    both say so.
    """
    return {
        "molecule_smiles": node["smiles"],
        "is_stock_available": node.get("in_stock", False),
        "reactions": [
            {
                "reactants": [_molecule_node(mol) for mol in rxn.get("children", [])],
                "template_used": rxn.get("metadata", {}).get("template_code"),
                "template_hash": rxn.get("metadata", {}).get("template_hash"),
                "template_smarts": rxn.get("metadata", {}).get("template"),
                "template_occurrence": rxn.get("metadata", {}).get("library_occurence"),
                "score": rxn.get("metadata", {}).get("policy_probability"),
                "reaction_smiles": rxn.get("smiles", ""),
                "classification": rxn.get("metadata", {}).get("classification"),
            }
            for rxn in node.get("children", [])
        ],
    }


def _enrich_with_evidence(node: dict, conditions_service) -> dict:
    """Attach condition evidence to every reaction in a route tree.

    Wrapped so a failure anywhere in the evidence layer degrades that step to
    "unavailable" instead of failing the plan. A solved route stays solved even
    with the evidence database down: retrosynthesis does not depend on
    evidence retrieval.
    """
    for reaction in node.get("reactions", []):
        try:
            reaction["evidence"] = conditions_service.for_step(
                reaction, node["molecule_smiles"]
            )
        except Exception:
            logger.warning("evidence enrichment failed for a step", exc_info=False)
            reaction["evidence"] = {
                "evidence_level": "unavailable",
                "reason": (
                    "Evidence retrieval failed for this step, so no conclusion "
                    "about precedent can be drawn either way."
                ),
            }
        for child in reaction.get("reactants", []):
            _enrich_with_evidence(child, conditions_service)
    return node


def _enrich_with_validation(node: dict, structural_model, learned_model) -> dict:
    """Attach forward validation to every reaction in a route tree."""
    import concurrent.futures
    
    for reaction in node.get("reactions", []):
        target_smiles = node.get("molecule_smiles", "")
        reactants_smiles = [c.get("molecule_smiles", "") for c in reaction.get("reactants", [])]
        template_smarts = reaction.get("template_smarts")
        
        # Define tasks for concurrent execution
        def run_structural():
            try:
                if not structural_model: return None
                return structural_model.validate_step(
                    target_product_smiles=target_smiles,
                    reactants_smiles=reactants_smiles,
                    template_smarts=template_smarts,
                )
            except Exception as e:
                logger.warning("Structural validation failed", exc_info=True)
                return {"status": "VALIDATION_ERROR", "error": str(e)}

        def run_learned():
            try:
                if not learned_model: return None
                return learned_model.validate_step(
                    target_product_smiles=target_smiles,
                    reactants_smiles=reactants_smiles,
                )
            except Exception as e:
                logger.warning("Learned validation failed", exc_info=True)
                return {"status": "VALIDATION_ERROR", "error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_s = executor.submit(run_structural)
            future_l = executor.submit(run_learned)
            
            s_res = future_s.result()
            l_res = future_l.result()
            
        if s_res:
            reaction["structural_validation"] = s_res.to_dict() if hasattr(s_res, 'to_dict') else s_res
        if l_res:
            reaction["forward_validation"] = l_res.to_dict() if hasattr(l_res, 'to_dict') else l_res
            
        for child in reaction.get("reactants", []):
            _enrich_with_validation(child, structural_model, learned_model)
    return node


def _enrich_with_assessment(node: dict) -> dict:
    """Combine evidence and validation signals into a chemist-facing assessment."""
    from backend.retrosynthesis.assessment import generate_reaction_assessment
    
    for reaction in node.get("reactions", []):
        s_val = reaction.get("structural_validation", {})
        l_val = reaction.get("forward_validation", {})
        ev = reaction.get("evidence", {})
        
        assessment = generate_reaction_assessment(
            structural_status=s_val.get("status"),
            forward_status=l_val.get("status"),
            ord_level=ev.get("evidence_level")
        )
        reaction["assessment"] = assessment
        
        for child in reaction.get("reactants", []):
            _enrich_with_assessment(child)
            
    return node


def _evidence_summary(tree: dict) -> dict:
    """Route-level coverage: how much of this route is actually supported."""
    counts = {
        "experimental": 0,
        "similar_experimental": 0,
        "predicted": 0,
        "unavailable": 0,
    }
    sources = set()

    def walk(node: dict) -> None:
        for reaction in node.get("reactions", []):
            evidence = reaction.get("evidence") or {}
            level = evidence.get("evidence_level", "unavailable")
            counts[level] = counts.get(level, 0) + 1
            for bucket in ("direct_precedents", "similar_precedents"):
                for precedent in evidence.get(bucket, []):
                    prov = precedent.get("provenance") or {}
                    ident = (
                        prov.get("doi")
                        or prov.get("patent_number")
                        or prov.get("source_id")
                    )
                    if ident:
                        sources.add(ident)
            for child in reaction.get("reactants", []):
                walk(child)

    walk(tree)
    total = sum(counts.values())
    supported = counts["experimental"] + counts["similar_experimental"]
    return {
        "steps": total,
        "steps_with_experimental_evidence": counts["experimental"],
        "steps_with_similar_evidence": counts["similar_experimental"],
        "steps_predicted": counts["predicted"],
        "steps_without_evidence": counts["unavailable"],
        "evidence_coverage": round(supported / total, 3) if total else 0.0,
        #  Distinct source records behind the matched precedents - a DOI or
        #  patent number counted once. Not a count of the literature.
        "distinct_sources": len(sources),
    }


def _validation_summary(tree: dict) -> dict:
    """Route-level coverage: how many steps were successfully validated."""
    counts = {
        "MATCH": 0,
        "PARTIAL_MATCH": 0,
        "MISMATCH": 0,
        "MODEL_UNAVAILABLE": 0,
        "VALIDATION_ERROR": 0,
    }
    
    def walk(node: dict) -> None:
        for reaction in node.get("reactions", []):
            s_val = reaction.get("structural_validation") or {}
            l_val = reaction.get("forward_validation") or {}
            
            s_status = s_val.get("status", "VALIDATION_ERROR")
            if s_status in counts:
                counts[s_status] += 1
                
            l_status = l_val.get("status", "MODEL_UNAVAILABLE")
            if l_status in counts:
                counts[l_status] += 1
                
            for child in reaction.get("reactants", []):
                walk(child)

    walk(tree)
    # Total is based on structural steps
    total = sum(counts[k] for k in ["MATCH", "PARTIAL_MATCH", "MISMATCH", "VALIDATION_ERROR"] if counts.get(k))
    return {
        "steps_total": total,
        "steps_match": counts["MATCH"],
        "steps_partial_match": counts["PARTIAL_MATCH"],
        "steps_mismatch": counts["MISMATCH"],
        "steps_error": counts["MODEL_UNAVAILABLE"] + counts["VALIDATION_ERROR"],
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
        self._conditions = None
        self._forward_model = None
        self._learned_forward_model = None

    def _get_forward_model(self):
        if self._forward_model is None:
            try:
                from backend.retrosynthesis.validation import RDKitTemplateReversalModel
                self._forward_model = RDKitTemplateReversalModel()
            except Exception:
                logger.warning("structural model unavailable", exc_info=False)
                return None
        return self._forward_model
        
    def _get_learned_forward_model(self):
        if self._learned_forward_model is None:
            try:
                from backend.retrosynthesis.validation import MicroserviceLearnedForwardModel
                self._learned_forward_model = MicroserviceLearnedForwardModel()
            except Exception:
                logger.warning("learned forward model unavailable", exc_info=False)
                return None
        return self._learned_forward_model

    def _conditions_service(self):
        """Built on first use, not at startup: the evidence layer is optional and
        must never delay or block the retrosynthesis engine coming up."""
        if self._conditions is None:
            try:
                from backend.conditions.service import ConditionsService

                self._conditions = ConditionsService()
            except Exception:
                logger.warning("conditions service unavailable", exc_info=False)
                return None
        return self._conditions

    def plan_routes(
        self,
        smiles: str,
        top_n: int = 5,
        iteration_limit: int = DEFAULT_ITERATION_LIMIT,
        include_images: bool = False,
        include_conditions: bool = False,
        include_validation: bool = False,
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

            #  Enrichment runs AFTER the search, outside the engine, and only
            #  when asked for. Default-off keeps existing clients byte-identical
            #  and exactly as fast as before.
            if include_conditions and routes:
                conditions_service = self._conditions_service()
                if conditions_service is not None:
                    for entry in routes:
                        _enrich_with_evidence(entry["tree"], conditions_service)
                        entry["evidence_summary"] = _evidence_summary(entry["tree"])

            if include_validation and routes:
                structural_model = self._get_forward_model()
                learned_model = self._get_learned_forward_model()
                for entry in routes:
                    _enrich_with_validation(entry["tree"], structural_model, learned_model)
                    entry["validation_summary"] = _validation_summary(entry["tree"])

            if routes and (include_validation or include_conditions):
                from backend.retrosynthesis.route_assessment import aggregate_route_assessment
                for entry in routes:
                    _enrich_with_assessment(entry["tree"])
                    entry["assessment"] = aggregate_route_assessment(entry["tree"])

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
