"""Fetch the ESOL / Delaney aqueous solubility dataset."""
import hashlib
import sys
import urllib.request
from pathlib import Path

# DeepChem's MoleculeNet copy. The usual S3 URL
# (deepchem.data.s3-us-west-1.amazonaws.com) fails TLS verification - the dotted
# bucket name does not match the wildcard cert - and path-style 404s.
URL = "https://raw.githubusercontent.com/deepchem/deepchem/master/datasets/delaney-processed.csv"
OUT = Path(__file__).resolve().parents[1] / "data/external/esol/delaney-processed.csv"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(URL, timeout=120).read()
    OUT.write_bytes(data)
    rows = len(data.strip().split(b"\n")) - 1
    print(f"{OUT}  {OUT.stat().st_size} bytes  {rows} data rows")
    print(f"sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
