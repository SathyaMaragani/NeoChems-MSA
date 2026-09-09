import { useEffect, useRef, useState } from 'react'
import { Editor } from 'ketcher-react'
import { StandaloneStructServiceProvider } from 'ketcher-standalone'
import type { Ketcher } from 'ketcher-core'
import 'ketcher-react/dist/index.css'

// Runs the whole structure service in-browser (WASM) - no Ketcher server needed.
const structServiceProvider = new StandaloneStructServiceProvider()

const EXAMPLES: [string, string][] = [
  ['Aspirin', 'CC(=O)Oc1ccccc1C(=O)O'],
  ['Ibuprofen', 'CC(C)Cc1ccc(C(C)C(=O)O)cc1'],
  ['Paracetamol', 'CC(=O)Nc1ccc(O)cc1'],
]

type Props = {
  smiles: string
  onSmilesChange: (smiles: string) => void
}

export default function MoleculeEditor({ smiles, onSmilesChange }: Props) {
  const ketcherRef = useRef<Ketcher | null>(null)
  const [ready, setReady] = useState(false)
  // Set while we push a structure INTO Ketcher, so the resulting 'change' event
  // does not bounce straight back out and overwrite what the user is typing.
  const applying = useRef(false)
  // Last SMILES Ketcher itself produced; lets us skip re-loading its own output.
  const fromEditor = useRef<string | null>(null)

  // Text field -> canvas. Debounced: pushing every keystroke would send Ketcher a
  // stream of half-finished SMILES.
  useEffect(() => {
    if (!ready) return
    const ketcher = ketcherRef.current
    if (!ketcher) return
    if (smiles === fromEditor.current) return

    const timer = setTimeout(async () => {
      applying.current = true
      try {
        await ketcher.setMolecule(smiles.trim())
      } catch {
        // Partial or invalid SMILES while typing. The Represent tab reports
        // properly on what the backend thinks; no need to shout here.
      } finally {
        // Let Ketcher's own change events settle before listening again.
        setTimeout(() => {
          applying.current = false
        }, 150)
      }
    }, 600)
    return () => clearTimeout(timer)
  }, [smiles, ready])

  // Canvas -> text field.
  const handleInit = (ketcher: Ketcher) => {
    ketcherRef.current = ketcher
    setReady(true)
    ketcher.editor.subscribe('change', async () => {
      if (applying.current) return
      try {
        const drawn = (await ketcher.getSmiles()) ?? ''
        fromEditor.current = drawn
        onSmilesChange(drawn)
      } catch {
        /* editor mid-edit; ignore */
      }
    })
  }

  return (
    <>
      <div className="ketcher-host">
        <Editor
          staticResourcesUrl={import.meta.env.BASE_URL}
          structServiceProvider={structServiceProvider}
          errorHandler={(message: string) => console.error('[ketcher]', message)}
          onInit={handleInit}
        />
      </div>

      <label className="smiles-label" htmlFor="smiles-input">
        SMILES
      </label>
      <input
        id="smiles-input"
        className="smiles-input"
        value={smiles}
        placeholder="Paste a SMILES, or draw above"
        spellCheck={false}
        onChange={(event) => {
          fromEditor.current = null
          onSmilesChange(event.target.value)
        }}
      />

      <div className="examples">
        <span className="muted">Load:</span>
        {EXAMPLES.map(([name, value]) => (
          <button
            key={name}
            className="link-button"
            onClick={() => {
              fromEditor.current = null
              onSmilesChange(value)
            }}
          >
            {name}
          </button>
        ))}
        <button
          className="link-button"
          onClick={() => {
            fromEditor.current = null
            onSmilesChange('')
          }}
        >
          Clear
        </button>
      </div>

      <p className="status">
        {ready ? 'Editor ready' : 'Loading editor...'}
        {ready && ' - Ketcher rewrites SMILES in its own form; the canonical string comes from the backend.'}
      </p>
    </>
  )
}
