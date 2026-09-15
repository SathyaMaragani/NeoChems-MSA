import json
import random
import itertools
from rdkit import Chem
from rdkit.Chem import AllChem

random.seed(42)

def generate_dataset():
    # Basic building blocks
    pool_a = [
        "c1ccc(Br)cc1", "Cc1ccc(Br)cc1", "c1ccc(I)cc1", "c1ccc(Cl)cc1",
        "O=C(O)c1ccccc1", "O=C(O)c1ccc(Cl)cc1", "CC(=O)O", "c1ccc(N)cc1",
        "c1ccc(O)cc1", "c1ccc(C=O)cc1", "c1ccccc1"
    ]
    pool_b = [
        "OB(O)c1ccccc1", "OB(O)c1ccc(C)cc1", "OB(O)c1ccc(F)cc1",
        "c1ccc(N)cc1", "NCc1ccccc1", "c1ccc(O)cc1", "CCO", "CO",
        "c1ccc([Mg]Br)cc1", "CC(C)N"
    ]
    
    # Forward reaction SMARTS and corresponding Retro templates
    reactions = [
        {
            "name": "Suzuki",
            "forward": "[c:1]-[Br,I,Cl].[c:2]-[B]([O])[O] >> [c:1]-[c:2]",
            "retro": "[c:1]-[c:2] >> [c:1]-[Br].[c:2]-[B]([OH])[OH]"
        },
        {
            "name": "Amidation",
            "forward": "[C:1](=[O:2])-[OH].[N;H2,H1:3] >> [C:1](=[O:2])-[N:3]",
            "retro": "[C:1](=[O:2])-[N:3] >> [C:1](=[O:2])-[OH].[N;H2,H1:3]"
        },
        {
            "name": "Esterification",
            "forward": "[C:1](=[O:2])-[OH].[O;H1:3]-[C:4] >> [C:1](=[O:2])-[O:3]-[C:4]",
            "retro": "[C:1](=[O:2])-[O:3]-[C:4] >> [C:1](=[O:2])-[OH].[O;H1:3]-[C:4]"
        },
        {
            "name": "SNAr",
            "forward": "[c:1]-[F,Cl,Br].[O,N;H1,H2:2] >> [c:1]-[O,N:2]",
            "retro": "[c:1]-[O,N:2] >> [c:1]-[F].[O,N;H1,H2:2]"
        }
    ]
    
    dataset = []
    
    for rxn_def in reactions:
        rxn = AllChem.ReactionFromSmarts(rxn_def["forward"])
        for smi_a, smi_b in itertools.product(pool_a, pool_b):
            mol_a = Chem.MolFromSmiles(smi_a)
            mol_b = Chem.MolFromSmiles(smi_b)
            if not mol_a or not mol_b: continue
            
            # Forward synthesis
            products = rxn.RunReactants((mol_a, mol_b))
            if not products:
                # try reverse order
                products = rxn.RunReactants((mol_b, mol_a))
                if not products:
                    continue
                reactants_smiles = [smi_b, smi_a]
            else:
                reactants_smiles = [smi_a, smi_b]
                
            for product_tuple in products:
                pmol = product_tuple[0]
                Chem.SanitizeMol(pmol)
                target_smiles = Chem.MolToSmiles(pmol, isomericSmiles=True)
                
                # VALID CASE
                dataset.append({
                    "target": target_smiles,
                    "reactants": reactants_smiles,
                    "template": rxn_def["retro"],
                    "ground_truth": "MATCH",
                    "perturbation": "None",
                    "reaction_type": rxn_def["name"]
                })
                
                # PERTURBATION: INCOMPLETE ROUTE
                if random.random() < 0.5:
                    dataset.append({
                        "target": target_smiles,
                        "reactants": [reactants_smiles[0]], # Drop one
                        "template": rxn_def["retro"],
                        "ground_truth": "MISMATCH",
                        "perturbation": "IncompleteRoute",
                        "reaction_type": rxn_def["name"]
                    })
                    
                # PERTURBATION: REGIO/SKELETON SWAP
                # Just randomly attach a methyl somewhere else to simulate wrong regioisomer
                if pmol.GetNumAtoms() > 6 and random.random() < 0.3:
                    # simplistic: add a carbon to the first aromatic carbon that has a hydrogen
                    rwmol = Chem.RWMol(pmol)
                    added = False
                    for atom in rwmol.GetAtoms():
                        if atom.GetIsAromatic() and atom.GetTotalNumHs() > 0:
                            idx = rwmol.AddAtom(Chem.Atom(6))
                            rwmol.AddBond(atom.GetIdx(), idx, Chem.BondType.SINGLE)
                            added = True
                            break
                    if added:
                        Chem.SanitizeMol(rwmol)
                        bad_target = Chem.MolToSmiles(rwmol)
                        dataset.append({
                            "target": bad_target,
                            "reactants": reactants_smiles,
                            "template": rxn_def["retro"],
                            "ground_truth": "MISMATCH",
                            "perturbation": "RegioSkeleton",
                            "reaction_type": rxn_def["name"]
                        })
                        
    # Deduplicate
    unique = []
    seen = set()
    for d in dataset:
        key = (d["target"], tuple(d["reactants"]), d["perturbation"])
        if key not in seen:
            seen.add(key)
            unique.append(d)
            
    with open("calibration_set.json", "w") as f:
        json.dump(unique, f, indent=2)
        
    print(f"Generated {len(unique)} calibration examples.")
    counts = {"MATCH": 0, "MISMATCH": 0}
    for d in unique: counts[d["ground_truth"]] += 1
    print(f"Valid: {counts['MATCH']}, Invalid: {counts['MISMATCH']}")

if __name__ == "__main__":
    generate_dataset()
