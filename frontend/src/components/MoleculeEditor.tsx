import { useEffect, useRef, useState } from 'react'
import { Editor } from 'ketcher-react'
import { StandaloneStructServiceProvider } from 'ketcher-standalone'
import type { Ketcher } from 'ketcher-core'
import 'ketcher-react/dist/index.css'

// Runs the whole structure service in-browser (WASM) - no Ketcher server needed.
const structServiceProvider = new StandaloneStructServiceProvider()

type Props = {
  smiles: string
  onSmilesChange: (smiles: string) => void
  onReady?: (ready: boolean) => void
}

/** The canvas only. SMILES entry, examples and actions live in the workspace
 *  around it, so the editor can be the hero it is meant to be.
 *
 *  Mounted once and hidden with CSS rather than unmounted between workspace
 *  states: Ketcher boots a WASM structure service, so remounting costs seconds
 *  and would make "Edit molecule" feel like restarting the app. */
export default function MoleculeEditor({ smiles, onSmilesChange, onReady }: Props) {
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
        // Partial or invalid SMILES while typing; the workspace reports validity.
      } finally {
        setTimeout(() => {
          applying.current = false
        }, 150)
      }
    }, 600)
    return () => clearTimeout(timer)
  }, [smiles, ready])

  const handleInit = (ketcher: Ketcher) => {
    ketcherRef.current = ketcher
    setReady(true)
    onReady?.(true)
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
    <div className="editor-canvas">
      <Editor
        staticResourcesUrl={import.meta.env.BASE_URL}
        structServiceProvider={structServiceProvider}
        errorHandler={(message: string) => console.error('[ketcher]', message)}
        onInit={handleInit}
      />
      {!ready && (
        <div className="editor-boot">
          <span className="spinner" />
          <span>Starting structure editor…</span>
        </div>
      )}
    </div>
  )
}
