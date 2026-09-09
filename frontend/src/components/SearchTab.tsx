import { useEffect, useState } from 'react'
import {
  exactSearch,
  getMolecule,
  similaritySearch,
  substructureSearch,
  type Molecule,
  type SimilarityHit,
} from '../api'
import { EmptySmilesNotice, ErrorBox } from './Feedback'

type Mode = 'exact' | 'similarity' | 'substructure'

const MODES: [Mode, string][] = [
  ['exact', 'Exact'],
  ['similarity', 'Similarity'],
  ['substructure', 'Substructure'],
]

// Depictions are fetched per row and cached for the session, so switching tabs
// or re-running a search does not refetch images already on screen.
const imageCache = new Map<number, string>()

function Depiction({ id }: { id: number }) {
  const [src, setSrc] = useState<string | undefined>(imageCache.get(id))

  useEffect(() => {
    if (src) return
    let cancelled = false
    getMolecule(id)
      .then((molecule) => {
        if (cancelled || !molecule.image_png_base64) return
        imageCache.set(id, molecule.image_png_base64)
        setSrc(molecule.image_png_base64)
      })
      .catch(() => {
        /* a missing thumbnail should not break the row */
      })
    return () => {
      cancelled = true
    }
  }, [id, src])

  return (
    <div className="thumb">
      {src ? (
        <img src={`data:image/png;base64,${src}`} alt={`Molecule ${id}`} />
      ) : (
        <span className="muted">...</span>
      )}
    </div>
  )
}

function ResultsTable({ rows }: { rows: (Molecule & { tanimoto?: number })[] }) {
  return (
    <table className="results">
      <thead>
        <tr>
          <th>Structure</th>
          <th>ID</th>
          <th>Canonical SMILES</th>
          <th>MW</th>
          {rows.some((r) => r.tanimoto != null) && <th>Tanimoto</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              <Depiction id={row.id} />
            </td>
            <td>
              {row.id}
              {row.is_mineral_salt && (
                <div className="tag" title="Counter-ion is the active species; not parent-stripped">
                  mineral
                </div>
              )}
            </td>
            <td className="mono wrap">{row.canonical_smiles}</td>
            <td>{row.molecular_weight?.toFixed(1)}</td>
            {row.tanimoto != null && (
              <td className="score">{row.tanimoto.toFixed(4)}</td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function SearchTab({ smiles }: { smiles: string }) {
  const [mode, setMode] = useState<Mode>('similarity')
  const [rows, setRows] = useState<(Molecule & { tanimoto?: number })[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [topN, setTopN] = useState(10)
  const [minSimilarity, setMinSimilarity] = useState(0)

  const run = async () => {
    setBusy(true)
    setError(null)
    setRows(null)
    setNote(null)
    try {
      if (mode === 'exact') {
        const match = await exactSearch(smiles)
        setRows([match])
      } else if (mode === 'similarity') {
        const body = await similaritySearch(smiles, topN, minSimilarity)
        setRows(body.results as SimilarityHit[])
        setNote(`Query canonicalized to ${body.query_canonical_smiles}`)
      } else {
        const body = await substructureSearch(smiles, topN)
        setRows(body.results)
        setNote(`${body.count} match${body.count === 1 ? '' : 'es'} for pattern ${body.query_pattern}`)
      }
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <nav className="subtabs">
        {MODES.map(([value, label]) => (
          <button
            key={value}
            className={value === mode ? 'subtab active' : 'subtab'}
            onClick={() => {
              setMode(value)
              setRows(null)
              setError(null)
              setNote(null)
            }}
          >
            {label}
          </button>
        ))}
      </nav>

      <p className="tab-intro">
        {mode === 'exact' &&
          'Exact match on canonical SMILES, falling back to InChIKey. A salt form finds its parent record.'}
        {mode === 'similarity' &&
          'Tanimoto over Morgan fingerprints (radius 2, 2048 bits). Stereo-blind: enantiomers score 1.0 against each other.'}
        {mode === 'substructure' &&
          'Searches the database for molecules CONTAINING the drawn structure as a substructure - the current structure is the pattern, not the target.'}
      </p>

      {!smiles.trim() && <EmptySmilesNotice />}

      <div className="controls">
        <button className="primary" onClick={run} disabled={busy || !smiles.trim()}>
          {busy ? 'Searching...' : 'Search'}
        </button>

        {mode === 'similarity' && (
          <>
            <label>
              Top N
              <input
                type="number"
                min={1}
                max={200}
                value={topN}
                onChange={(e) => setTopN(Number(e.target.value))}
              />
            </label>
            <label>
              Min similarity
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={minSimilarity}
                onChange={(e) => setMinSimilarity(Number(e.target.value))}
              />
            </label>
          </>
        )}

        {mode === 'substructure' && (
          <label>
            Max results
            <input
              type="number"
              min={1}
              max={500}
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
            />
          </label>
        )}
      </div>

      <ErrorBox error={error} />
      {note && !error && <p className="muted mono small">{note}</p>}

      {rows && rows.length === 0 && (
        <div className="notice notice-warn">No matches in the database.</div>
      )}
      {rows && rows.length > 0 && <ResultsTable rows={rows} />}
    </div>
  )
}
