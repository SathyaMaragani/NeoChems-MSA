"""Applicability domain: how much like the training set is this molecule?

A regression model asked about chemistry unlike anything it trained on will still
return a confident-looking number. This is the signal that says not to trust it.

The measure is max Tanimoto similarity to the training set, scored with
backend.molrepr.search.rank_by_similarity - the same code path the molecule search
uses, so there is one Tanimoto implementation in the codebase, not two.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from backend.molrepr import search, service

# Conventional novelty boundary, NOT an error threshold. Calibration on the held-out
# scaffold test set (backend/qsar/calibrate.py) found NO relationship between this
# similarity and prediction error: Pearson -0.074, permutation p = 0.44, and every
# bootstrapped below/above RMSE ratio from 0.25 to 0.50 has a 95% CI spanning 1.0.
# So this measures whether a molecule is structurally familiar, which is a real and
# useful thing to know, and does NOT claim the prediction is reliable.
DEFAULT_THRESHOLD = 0.4

NOTE = (
    "max_train_similarity measures structural novelty only. Calibration on the "
    "held-out test set found no relationship between it and prediction error "
    "(Pearson -0.074, permutation p=0.44), so a familiar molecule is NOT thereby "
    "a reliable prediction. For expected error use prediction_interval, whose "
    "empirical_coverage was measured on held-out data."
)

# How many neighbours to keep for reporting.
N_NEIGHBOURS = 3


@dataclass
class Applicability:
    max_train_similarity: float
    # Deliberately not called `in_domain`: that name asserts the prediction is
    # trustworthy, which the calibration does not support.
    structurally_familiar: bool
    threshold: float
    nearest_training_smiles: list[str]
    note: str = NOTE

    def to_dict(self) -> dict:
        return asdict(self)


class ApplicabilityIndex:
    """Precomputed training-set fingerprints, held for the life of the process."""

    def __init__(self, training_smiles: list[str], threshold: float = DEFAULT_THRESHOLD):
        self.threshold = threshold
        self.training_smiles = training_smiles
        # Stereo-blind fingerprints on purpose: "is this molecule structurally
        # familiar" is a scaffold question, and the chirality-aware bits would call
        # an enantiomer of a training compound unfamiliar when it plainly is not.
        self._candidates = [
            (smiles, service.morgan_fingerprint(smiles)) for smiles in training_smiles
        ]

    def score(self, smiles: str) -> Applicability:
        query_fp = service.morgan_fingerprint(smiles)
        ranked = search.rank_by_similarity(
            query_fp, self._candidates, top_n=N_NEIGHBOURS
        )
        best = ranked[0][1] if ranked else 0.0
        return Applicability(
            max_train_similarity=round(float(best), 4),
            structurally_familiar=bool(best >= self.threshold),
            threshold=self.threshold,
            nearest_training_smiles=[smiles for smiles, _ in ranked],
        )
