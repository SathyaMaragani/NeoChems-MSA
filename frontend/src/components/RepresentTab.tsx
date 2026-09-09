import { useState } from 'react'
import { represent, type Representation } from '../api'
import { EmptySmilesNotice, ErrorBox } from './Feedback'

export default function RepresentTab({ smiles }: { smiles: string }) {
  const [result, setResult] = useState<Representation | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(await represent(smiles))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <p className="tab-intro">
        Canonicalize the current structure without touching the database. Useful for
        checking the editor is producing what you expect before searching.
      </p>

      {!smiles.trim() && <EmptySmilesNotice />}

      <button className="primary" onClick={run} disabled={busy || !smiles.trim()}>
        {busy ? 'Working...' : 'Represent'}
      </button>

      <ErrorBox error={error} />

      {result && (
        <div className="result">
          <div className="depiction">
            <img
              src={`data:image/png;base64,${result.image_png_base64}`}
              alt={`Structure of ${result.canonical_smiles}`}
            />
          </div>
          <dl className="facts">
            <dt>Canonical SMILES</dt>
            <dd className="mono">{result.canonical_smiles}</dd>

            {result.canonical_smiles !== result.smiles_as_given_canonical && (
              <>
                <dt>As given (not parent-stripped)</dt>
                <dd className="mono">
                  {result.smiles_as_given_canonical}
                  <span className="muted"> - salt/charge stripped for the canonical form</span>
                </dd>
              </>
            )}

            <dt>InChIKey</dt>
            <dd className="mono">{result.inchikey}</dd>

            <dt>Molecular weight</dt>
            <dd>{result.molecular_weight.toFixed(3)}</dd>

            <dt>Morgan fingerprint</dt>
            <dd>
              radius {result.morgan_radius}, {result.morgan_n_bits} bits,{' '}
              {result.morgan_fingerprint_on_bits.length} bits set
            </dd>
          </dl>
        </div>
      )}
    </div>
  )
}
