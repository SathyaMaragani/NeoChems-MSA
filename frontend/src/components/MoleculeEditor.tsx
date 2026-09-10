import { useEffect, useRef, useState } from 'react'
import { Editor } from 'ketcher-react'
import { StandaloneStructServiceProvider } from 'ketcher-standalone'
import type { Ketcher } from 'ketcher-core'
import 'ketcher-react/dist/index.css'

// Runs the whole structure service in-browser (WASM) - no Ketcher server needed.
const structServiceProvider = new StandaloneStructServiceProvider()

/** Frame a newly loaded structure. Ketcher persists the last zoom level, so a
 *  molecule loaded after someone zoomed out can arrive at 10% and read as an
 *  empty canvas. Only called when WE load a structure - zooming by hand while
 *  editing is left alone. */
function fitView(ketcher: Ketcher) {
  try {
    const editor = ketcher.editor as unknown as {
      zoom?: (value?: number) => number
      centerViewportAccordingToStruct?: () => void
    }
    // editor.zoom(1) applies; ketcher.setZoom(1) silently no-ops in 3.18.
    editor.zoom?.(1)
    // centerViewportAccordingToStruct moves the viewport; centerStruct does not.
    editor.centerViewportAccordingToStruct?.()
  } catch {
    // Zoom helpers are best-effort; a wrong frame is not worth breaking load over.
  }
}

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
        fitView(ketcher)
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
    // Handy for debugging in the console, and what ketcher-react itself expects.
    ;(window as unknown as { ketcher?: Ketcher }).ketcher = ketcher
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
