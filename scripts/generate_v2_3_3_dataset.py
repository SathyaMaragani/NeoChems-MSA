import pandas as pd
import re
import json
import random
from pathlib import Path
from rdkit import Chem

random.seed(42)

def generate_v2_3_3_dataset():
    data_dir = Path("data/external/ord")
    parquet_files = list(data_dir.glob("*.parquet"))
    
    real_reactions = set()
    
    print(f"Found {len(parquet_files)} parquet files. Extracting reactions...")
    
    for pf in parquet_files:
        df = pd.read_parquet(pf)
        for _, row in df.iterrows():
            pb = row['reaction']
            text = pb.decode('utf-8', errors='ignore')
            # Extract standard reaction SMILES from ORD protobuf representation (Precursors>>Products)
            match = re.search(r'([A-Za-z0-9#\(\)\[\]\+\-\=\.\@\\]+>>[A-Za-z0-9#\(\)\[\]\+\-\=\.\@\\]+)', text)
            if match:
                rxn_smiles = match.group(1)
                parts = rxn_smiles.split(">>")
                if len(parts) == 2 and parts[0] and parts[1]:
                    precursors, target = parts
                    # basic length filter to avoid trivial or highly complex mixtures
                    if 10 < len(precursors) < 200 and 10 < len(target) < 150:
                        real_reactions.add((precursors, target))
            
            if len(real_reactions) >= 1000:
                break
        if len(real_reactions) >= 1000:
            break
            
    print(f"Extracted {len(real_reactions)} real experimental reactions.")
    
    dataset = []
    
    # Tier A & B generation
    for precursors, target in list(real_reactions)[:500]:
        reactants = precursors.split(".")
        
        # Canonicalize target to ensure ground truth is clean
        mol = Chem.MolFromSmiles(target)
        if not mol:
            continue
        canon_target = Chem.MolToSmiles(mol, isomericSmiles=True)
        
        # VALID CASE (MATCH)
        dataset.append({
            "target": canon_target,
            "reactants": reactants,
            "ground_truth": "MATCH",
            "perturbation": "None"
        })
        
        # INVALID CASE (MISMATCH) - Adversarial variant
        p_type = random.choice(["MissingReactant", "WrongCouplingProduct", "Regioisomer"])
        
        bad_target = None
        bad_reactants = list(reactants)
        
        if p_type == "MissingReactant" and len(reactants) > 1:
            bad_reactants = reactants[:-1] # drop one reactant
            bad_target = canon_target
            
        elif p_type == "WrongCouplingProduct":
            # Just pick another product from the set to simulate a wildly wrong product
            other_rxn = random.choice(list(real_reactions))
            bad_target = other_rxn[1]
            
        elif p_type == "Regioisomer":
            # Shift a bond or just add a carbon
            rwmol = Chem.RWMol(mol)
            added = False
            for atom in rwmol.GetAtoms():
                if atom.GetIsAromatic() and atom.GetTotalNumHs() > 0:
                    idx = rwmol.AddAtom(Chem.Atom(6))
                    rwmol.AddBond(atom.GetIdx(), idx, Chem.BondType.SINGLE)
                    added = True
                    break
            if added:
                Chem.SanitizeMol(rwmol)
                bad_target = Chem.MolToSmiles(rwmol, isomericSmiles=True)
                
        if bad_target:
            dataset.append({
                "target": bad_target,
                "reactants": bad_reactants,
                "ground_truth": "MISMATCH",
                "perturbation": p_type
            })
            
    with open("real_calibration_set.json", "w") as f:
        json.dump(dataset, f, indent=2)
        
    counts = {"MATCH": 0, "MISMATCH": 0}
    for d in dataset: counts[d["ground_truth"]] += 1
    print(f"Saved {len(dataset)} calibration cases (Valid: {counts['MATCH']}, Invalid: {counts['MISMATCH']}) to real_calibration_set.json")

if __name__ == "__main__":
    generate_v2_3_3_dataset()
