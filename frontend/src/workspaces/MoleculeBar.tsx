import { Depiction } from '../components/MoleculeCard'

const EXAMPLES: [string, string][] = [
  ['Aspirin', 'CC(=O)Oc1ccccc1C(=O)O'],
  ['Ibuprofen', 'CC(C)Cc1ccc(C(C)C(=O)O)cc1'],
  ['Paracetamol', 'CC(=O)Nc1ccc(O)cc1'],
  ['Naproxen', 'COc1ccc2cc(C(C)C(=O)O)ccc2c1'],
]

/** Compact molecule input for the workspaces that analyse a structure rather
 *  than draw one. Drawing lives in Retrosynthesis, where the canvas is the hero;
 *  duplicating a second Ketcher instance here would cost another WASM boot. */
export default function MoleculeBar({
  smiles,
  onSmilesChange,
  onOpenEditor,
}: {
  smiles: string
  onSmilesChange: (value: string) => void
  onOpenEditor: () => void
}) {
  const trimmed = smiles.trim()
  return (
    <section className="molecule-bar">
      <div className="molecule-bar-thumb">
        {trimmed ? (
          <Depiction smiles={trimmed} alt="Current molecule" />
        ) : (
          <span className="molecule-bar-empty">No structure</span>
        )}
      </div>

      <div className="molecule-bar-main">
        <label className="field">
          <span className="eyebrow">SMILES</span>
          <input
            className="smiles-input"
            value={smiles}
            spellCheck={false}
            placeholder="Paste a SMILES, or draw one in Retrosynthesis"
            onChange={(event) => onSmilesChange(event.target.value)}
          />
        </label>
        <div className="quick-add-chips">
          {EXAMPLES.map(([name, value]) => (
            <button key={name} className="chip" onClick={() => onSmilesChange(value)}>
              {name}
            </button>
          ))}
          {trimmed && (
            <button className="chip" onClick={() => onSmilesChange('')}>
              Clear
            </button>
          )}
        </div>
      </div>

      <button className="ghost" onClick={onOpenEditor}>
        Draw structure ✎
      </button>
    </section>
  )
}
