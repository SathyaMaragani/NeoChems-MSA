import { useEffect, useMemo, useState } from 'react'
import {
  listProperties,
  predictProperty,
  type CoverageRow,
  type Prediction,
  type PropertySpec,
} from '../api'
import { EmptySmilesNotice, ErrorBox } from './Feedback'

/** Horizontal bar: the interval as a span, with the point estimate marked.
 *  A +/-1.3 log-unit interval reads as abstract in text and obvious as a bar. */
function IntervalBar({
  lower,
  upper,
  value,
  units,
}: {
  lower: number
  upper: number
  value: number
  units: string
}) {
  // Pad the axis either side so the interval never touches the ends.
  const span = upper - lower
  const axisMin = lower - span * 0.2
  const axisMax = upper + span * 0.2
  const pct = (x: number) => ((x - axisMin) / (axisMax - axisMin)) * 100

  return (
    <div className="interval">
      <div className="interval-track">
        <div
          className="interval-span"
          style={{ left: `${pct(lower)}%`, width: `${pct(upper) - pct(lower)}%` }}
        />
        <div className="interval-point" style={{ left: `${pct(value)}%` }} />
      </div>
      <div className="interval-labels">
        <span>{lower.toFixed(2)}</span>
        <span className="interval-value">
          {value.toFixed(2)} {units}
        </span>
        <span>{upper.toFixed(2)}</span>
      </div>
    </div>
  )
}

function coverageLabel(row: CoverageRow): string {
  // Deliberately the MEASURED coverage, not the nominal. A dropdown reading
  // "90%" would undo the point of measuring it.
  return `α = ${row.alpha} — measured ${(row.empirical * 100).toFixed(0)}% coverage`
}

export default function QsarTab({ smiles }: { smiles: string }) {
  const [specs, setSpecs] = useState<PropertySpec[] | null>(null)
  const [specError, setSpecError] = useState<unknown>(null)
  const [property, setProperty] = useState<string>('')
  const [model, setModel] = useState<string>('')
  const [alpha, setAlpha] = useState<number>(0.1)
  const [result, setResult] = useState<Prediction | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  // Everything selectable comes from the API - no property, model or alpha list
  // is hardcoded here, so a new property appears without a frontend change.
  useEffect(() => {
    listProperties()
      .then((body) => {
        setSpecs(body.properties)
        const first = body.properties[0]
        if (first) {
          setProperty(first.property)
          setModel(first.default_model)
          if (!first.calibrated_alphas.includes(alpha)) {
            setAlpha(first.calibrated_alphas[0])
          }
        }
      })
      .catch(setSpecError)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const spec = useMemo(
    () => specs?.find((s) => s.property === property),
    [specs, property],
  )
  const modelSpec = useMemo(
    () => spec?.models.find((m) => m.model === model),
    [spec, model],
  )
  const coverageRows = modelSpec?.coverage ?? []

  const run = async () => {
    if (!spec) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(await predictProperty(smiles, spec.property, model || undefined, alpha))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (specError) return <ErrorBox error={specError} />
  if (!specs) return <p className="muted">Loading available properties…</p>

  const interval = result?.prediction_interval
  const applicability = result?.applicability

  return (
    <div>
      <p className="tab-intro">
        Predicts physicochemical properties from structure, with a conformal
        prediction interval calibrated on held-out data.
      </p>

      {/* Permanent, not dismissible: whoever reads a number in a browser is not
          whoever read the README. */}
      <div className="notice notice-warn">
        <strong>Not experimentally validated</strong>
        <span>
          Trained on public data only, and never checked against a measurement made
          here. Intervals also <em>under-cover</em> their nominal label — the
          scaffold split puts calibration and test molecules in different regions of
          chemical space, which breaks the assumption conformal relies on. Always
          read the measured coverage, not the nominal one.
        </span>
      </div>

      {!smiles.trim() && <EmptySmilesNotice />}

      <div className="controls">
        <button className="primary" onClick={run} disabled={busy || !smiles.trim()}>
          {busy ? 'Predicting…' : 'Predict'}
        </button>

        <label>
          Property
          <select
            value={property}
            onChange={(e) => {
              const next = specs.find((s) => s.property === e.target.value)
              setProperty(e.target.value)
              setResult(null)
              if (next) {
                setModel(next.default_model)
                if (!next.calibrated_alphas.includes(alpha)) {
                  setAlpha(next.calibrated_alphas[0])
                }
              }
            }}
          >
            {specs.map((s) => (
              <option key={s.property} value={s.property}>
                {s.property} ({s.units})
              </option>
            ))}
          </select>
        </label>

        <label>
          Model
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {spec?.models.map((m) => (
              <option key={m.model} value={m.model}>
                {m.model} (RMSE {m.test_rmse.toFixed(2)})
              </option>
            ))}
          </select>
        </label>

        <label className="wide">
          Confidence level
          <select value={alpha} onChange={(e) => setAlpha(Number(e.target.value))}>
            {coverageRows.map((row) => (
              <option key={row.alpha} value={row.alpha}>
                {coverageLabel(row)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {spec && (
        <p className="muted small">
          {spec.description} · trained on {spec.dataset}
          {modelSpec && ` · ${modelSpec.description}`}
        </p>
      )}

      <ErrorBox error={error} />

      {result && interval && applicability && (
        <div className="result">
          <div className="prediction-headline">
            <span className="prediction-value">
              {result.predicted_value.toFixed(2)}
            </span>
            <span className="prediction-units">{result.units}</span>
          </div>

          <IntervalBar
            lower={interval.lower}
            upper={interval.upper}
            value={result.predicted_value}
            units={result.units}
          />

          <dl className="facts">
            <dt>Prediction interval</dt>
            <dd>
              {interval.lower.toFixed(2)} to {interval.upper.toFixed(2)}{' '}
              {result.units}
              <span className="muted">
                {' '}
                (width {(interval.upper - interval.lower).toFixed(2)})
              </span>
            </dd>

            <dt>Measured coverage</dt>
            <dd>
              <strong>{(interval.empirical_coverage * 100).toFixed(0)}%</strong>
              {interval.empirical_coverage_ci_95.length === 2 && (
                <span className="muted">
                  {' '}
                  (95% CI{' '}
                  {(interval.empirical_coverage_ci_95[0] * 100).toFixed(0)}–
                  {(interval.empirical_coverage_ci_95[1] * 100).toFixed(0)}%, n ={' '}
                  {interval.n_test})
                </span>
              )}
              <div className="muted small">
                Nominal label is {(interval.nominal_coverage * 100).toFixed(0)}%.
                Coverage is marginal across the test set — it is not a
                confidence for this particular molecule.
              </div>
            </dd>

            <dt>
              Structural similarity to training data
              <span
                className="hint"
                title="Max Tanimoto to the training set. Calibration found NO relationship between this and prediction error (p = 0.44), so it is not a reliability signal."
              >
                ?
              </span>
            </dt>
            <dd>
              {applicability.max_train_similarity.toFixed(2)}{' '}
              <span className="muted">
                ({applicability.structurally_familiar ? 'familiar' : 'novel'} vs
                threshold {applicability.threshold})
              </span>
              <div className="muted small">
                Does <em>not</em> predict accuracy — measured and found unrelated to
                error. Nearest training molecule:{' '}
                <code>{applicability.nearest_training_smiles[0]}</code>
              </div>
            </dd>

            <dt>Model performance (secondary)</dt>
            <dd className="muted">
              test RMSE {result.model_performance.test_rmse.toFixed(2)} · MAE{' '}
              {result.model_performance.test_mae.toFixed(2)} · R²{' '}
              {result.model_performance.test_r2.toFixed(2)} ·{' '}
              {result.model_performance.split} split · {interval.method}
            </dd>
          </dl>
        </div>
      )}
    </div>
  )
}
