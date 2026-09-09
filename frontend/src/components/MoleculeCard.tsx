import { useEffect, useState } from 'react'
import { represent } from '../api'

/** Depictions are stable for a SMILES, so cache them for the session rather than
 *  re-rendering the same structure on every route expand. */
const cache = new Map<string, string>()
const inFlight = new Map<string, Promise<string | null>>()

function fetchDepiction(smiles: string): Promise<string | null> {
  const hit = cache.get(smiles)
  if (hit) return Promise.resolve(hit)
  let pending = inFlight.get(smiles)
  if (!pending) {
    pending = represent(smiles)
      .then((body) => {
        cache.set(smiles, body.image_png_base64)
        return body.image_png_base64
      })
      .catch(() => null)
      .finally(() => inFlight.delete(smiles))
    inFlight.set(smiles, pending)
  }
  return pending
}

export function Depiction({ smiles, alt }: { smiles: string; alt?: string }) {
  const [src, setSrc] = useState<string | null>(() => cache.get(smiles) ?? null)

  useEffect(() => {
    let live = true
    if (cache.has(smiles)) {
      setSrc(cache.get(smiles)!)
      return
    }
    setSrc(null)
    fetchDepiction(smiles).then((value) => {
      if (live) setSrc(value)
    })
    return () => {
      live = false
    }
  }, [smiles])

  if (!src) return <div className="depiction-skeleton" aria-hidden />
  return <img className="depiction-img" src={`data:image/png;base64,${src}`} alt={alt ?? smiles} />
}

export function MoleculeCard({
  smiles,
  role,
  inStock,
  large = false,
}: {
  smiles: string
  role: string
  inStock?: boolean
  /** Results own the full workspace now, so molecules can be big enough to
   *  actually inspect. */
  large?: boolean
}) {
  const [copied, setCopied] = useState(false)
  return (
    <figure className={`mol-card${large ? ' large' : ''}`}>
      <div className="mol-card-head">
        <span className={`role-chip ${role.toLowerCase()}`}>{role}</span>
        {inStock !== undefined && (
          <span className={`stock-chip ${inStock ? 'in' : 'out'}`}>
            {inStock ? 'In stock' : 'Not in stock'}
          </span>
        )}
      </div>
      <div className="mol-card-body">
        <Depiction smiles={smiles} />
      </div>
      <figcaption>
        <code title={smiles}>{smiles}</code>
        <button
          className="icon-button"
          title="Copy SMILES"
          onClick={() => {
            navigator.clipboard?.writeText(smiles)
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
          }}
        >
          {copied ? 'copied' : 'copy'}
        </button>
      </figcaption>
    </figure>
  )
}
