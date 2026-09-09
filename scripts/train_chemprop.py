"""Train a Chemprop D-MPNN on the exported scaffold split.

MUST run in the `qsar-chemprop` conda env, not `retrosynth`: chemprop wants
rdkit>=2026 and networkx>=3, which would break AiZynthFinder's rdkit<2024 and
networkx<3 pins and the Postgres cartridge version coupling.

    conda activate qsar-chemprop
    python scripts/train_chemprop.py [--split scaffold] [--epochs 50]
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external/esol"
MODEL_DIR = ROOT / "models/qsar/solubility"

# The `chemprop` console script is not on PATH inside a subprocess launched from
# this interpreter, so call the CLI module through the running interpreter.
# `python -m chemprop.cli.main` imports but never runs - the module has no
# __main__ guard. The console script in this env's Scripts dir is the real entry
# point, and is not on PATH for a subprocess launched from here.
CHEMPROP = [str(Path(sys.executable).parent / "Scripts" / "chemprop.exe")]


def device_report() -> tuple[str, str]:
    """Which device will Lightning actually get? Blackwell (sm_120) needs a CUDA
    12.8+ torch build; an older wheel reports is_available() True and then dies
    with 'no kernel image available', so run a real op rather than trusting the flag.
    """
    import torch

    lines = [f"torch {torch.__version__}", f"cuda build {torch.version.cuda}"]
    if not torch.cuda.is_available():
        lines.append("cuda NOT available -> CPU")
        return "cpu", " | ".join(lines)
    name = torch.cuda.get_device_name(0)
    capability = torch.cuda.get_device_capability(0)
    lines.append(f"{name} sm_{capability[0]}{capability[1]}")
    try:
        a = torch.randn(256, 256, device="cuda")
        (a @ a).sum().item()
        torch.cuda.synchronize()
        lines.append("matmul OK -> GPU")
        return "gpu", " | ".join(lines)
    except Exception as err:  # kernels missing for this arch
        lines.append(f"matmul FAILED ({type(err).__name__}) -> falling back to CPU")
        return "cpu", " | ".join(lines)


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    residual = actual - predicted
    rmse = float(np.sqrt(np.mean(residual**2)))
    mae = float(np.mean(np.abs(residual)))
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    return {"rmse": rmse, "mae": mae, "r2": 1.0 - ss_res / ss_tot}


def read_column(path: Path, column: str) -> np.ndarray:
    with path.open(encoding="utf-8") as fh:
        return np.array([float(row[column]) for row in csv.DictReader(fh)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="scaffold", choices=["scaffold", "random"])
    parser.add_argument("--epochs", type=int, default=50)
    # 8GB VRAM, but ESOL is ~900 molecules; this is nowhere near the limit.
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()

    accelerator, report = device_report()
    print(f"device: {report}\n")

    out_dir = MODEL_DIR / f"chemprop_{args.split}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    train_cmd = [
        *CHEMPROP, "train",
        # One file plus a split column: chemprop 2.3 has no --separate-*-path,
        # and letting it re-split would break comparability with the baselines.
        "--data-path", str(DATA / f"{args.split}_all.csv"),
        "--splits-column", "split",
        "--task-type", "regression",
        "--smiles-columns", "smiles",
        "--target-columns", "logSolubility",
        "--output-dir", str(out_dir),
        "--epochs", str(args.epochs),
        "--batch-size", str(args.batch_size),
        "--accelerator", accelerator,
        "--num-workers", "0",  # Windows: worker processes re-import and stall
    ]
    print(" ".join(train_cmd), "\n")
    started = time.time()
    result = subprocess.run(train_cmd, cwd=ROOT)
    wall = time.time() - started
    if result.returncode != 0:
        print(f"training failed with exit {result.returncode}")
        return result.returncode
    print(f"\ntraining wall clock: {wall:.1f}s on {accelerator.upper()}")

    checkpoints = sorted(out_dir.rglob("*.ckpt"))
    if not checkpoints:
        print("no checkpoint produced")
        return 1
    checkpoint = checkpoints[-1]
    print(f"checkpoint: {checkpoint.relative_to(ROOT)}")

    test_path = DATA / f"{args.split}_test.csv"
    preds_path = out_dir / "test_preds.csv"
    predict_cmd = [
        *CHEMPROP, "predict",
        "--test-path", str(test_path),
        "--model-paths", str(checkpoint),
        "--preds-path", str(preds_path),
        "--smiles-columns", "smiles",
        "--accelerator", accelerator,
    ]
    print("\n" + " ".join(predict_cmd) + "\n")
    if subprocess.run(predict_cmd, cwd=ROOT).returncode != 0:
        return 1

    actual = read_column(test_path, "logSolubility")
    with preds_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # chemprop copies the input columns through and appends pred_N, so picking
    # "the column that isn't smiles" silently grabs the ground truth and scores
    # a perfect R2. Take the prediction column by name.
    pred_columns = [c for c in rows[0] if c.startswith("pred")]
    if not pred_columns:
        print(f"no pred_* column in {preds_path}; got {list(rows[0])}")
        return 1
    pred_column = pred_columns[0]
    predicted = np.array([float(r[pred_column]) for r in rows])

    scores = metrics(actual, predicted)
    print("=" * 56)
    print(f"  chemprop / {args.split} split / {args.epochs} epochs")
    print(f"  device        {accelerator.upper()}")
    print(f"  wall clock    {wall:.1f}s")
    print(f"  RMSE {scores['rmse']:.3f}   MAE {scores['mae']:.3f}   "
          f"R2 {scores['r2']:.3f}")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    sys.exit(main())
