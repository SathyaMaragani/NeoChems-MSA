import { useState } from 'react'
import { Depiction, MoleculeCard } from '../components/MoleculeCard'
import { AlertIcon, ArrowRightIcon } from '../components/icons'
import type {
  Molecule,
  Plan,
  Prediction,
  Reaction,
  Representation,
  RouteNode,
} from '../api'

/* ---------------- retrosynthesis ---------------- */

function toSteps(node: RouteNode): { reaction: Reaction; product: RouteNode }[] {
  const steps: { reaction: Reaction; product: RouteNode }[] = []
  const walk = (mol: RouteNode) => {
    for (const reaction of mol.reactions) {
      for (const child of reaction.reactants) walk(child)
      steps.push({ reaction, product: mol })
    }
  }
  walk(node)
  return steps
}

function leavesOf(node: RouteNode): RouteNode[] {
  const out: RouteNode[] = []
  const walk = (mol: RouteNode) => {
    if (!mol.reactions.length) out.push(mol)
    mol.reactions.forEach((r) => r.reactants.forEach(walk))
  }
  walk(node)
  return out
}

export function RetroResults({ plan }: { plan: Plan }) {
  const [selected, setSelected] = useState(0)
  const route = plan.routes[selected]
  const steps = route ? toSteps(route.tree) : []

  if (!plan.is_solved) {
    return (
      <div className="empty-state">
        <AlertIcon />
        <h3>No feasible route found</h3>
        <p>
          Nothing solved in {plan.iterations_used} iterations (
          {plan.search_time_seconds}s). Only routes that bottom out in purchasable
          material are returned, so an unsolved search returns nothing rather than a
          partial guess.
        </p>
        <ul>
          <li>Raise search depth under options — currently {plan.iteration_limit}, max 500</li>
          <li>Try a simpler or more common target</li>
          <li>This target may sit outside USPTO-derived chemistry altogether</li>
        </ul>
      </div>
    )
  }

  return (
    <>
      <div className="route-rail">
        {plan.routes.map((r, index) => (
          <button
            key={r.route_id}
            className={`route-tile${index === selected ? ' active' : ''}`}
            onClick={() => setSelected(index)}
          >
            <span className="route-tile-name">Route {index + 1}</span>
            <span className="route-tile-tag">
              {index === 0 ? 'Highest score' : 'Alternative'}
            </span>
            <span className="route-tile-score">
              {r.state_score?.toFixed(4)} · {r.number_of_reactions} step
              {r.number_of_reactions === 1 ? '' : 's'}
            </span>
          </button>
        ))}
      </div>

      {route && (
        <article className="route-panel">
          <header>
            <div>
              <h2>Route {selected + 1}</h2>
              <span className={`badge ${selected === 0 ? 'best' : 'alt'}`}>
                {selected === 0 ? '✓ Highest score' : 'Alternative'}
              </span>
            </div>
            <dl className="route-metrics">
              <div>
                <dt>State score</dt>
                <dd>{route.state_score?.toFixed(4) ?? '—'}</dd>
              </div>
              <div>
                <dt>Steps</dt>
                <dd>{route.number_of_reactions}</dd>
              </div>
              <div>
                <dt>Starting materials</dt>
                <dd>{leavesOf(route.tree).length} · all purchasable</dd>
              </div>
              <div>
                <dt>Weakest policy</dt>
                <dd>
                  {steps.length
                    ? Math.min(...steps.map((s) => s.reaction.score ?? 1)).toFixed(4)
                    : '—'}
                </dd>
              </div>
            </dl>
          </header>

          <div className="route-steps-full">
            {steps.map((step, index) => (
              <section className="rstep" key={`${step.reaction.reaction_smiles}-${index}`}>
                <div className="rstep-label">
                  Step {index + 1} of {steps.length}
                </div>
                <div className="rstep-flow">
                  <div className="rstep-group">
                    {step.reaction.reactants.map((reactant) => (
                      <MoleculeCard
                        key={reactant.molecule_smiles}
                        smiles={reactant.molecule_smiles}
                        role="Reactant"
                        inStock={reactant.is_stock_available}
                        large
                      />
                    ))}
                  </div>
                  <div className="rstep-arrow">
                    <ArrowRightIcon />
                    <span>
                      template {step.reaction.template_used ?? '—'}
                      {step.reaction.score != null &&
                        ` · policy ${step.reaction.score.toFixed(4)}`}
                    </span>
                  </div>
                  <div className="rstep-group">
                    <MoleculeCard
                      smiles={step.product.molecule_smiles}
                      role="Product"
                      large
                    />
                  </div>
                </div>
                <details className="rstep-detail">
                  <summary>Reaction detail</summary>
                  <dl>
                    <dt>Reaction SMILES</dt>
                    <dd>
                      <code>{step.reaction.reaction_smiles}</code>
                    </dd>
                    {step.reaction.template_smarts && (
                      <>
                        <dt>Template SMARTS</dt>
                        <dd>
                          <code>{step.reaction.template_smarts}</code>
                        </dd>
                      </>
                    )}
                  </dl>
                </details>
              </section>
            ))}
          </div>

          <footer className="route-footnote">
            State score measures how completely the route bottoms out in purchasable
            material, and policy is the expansion model&apos;s probability for that
            template. Neither is a yield, cost or feasibility prediction — this build
            has no model for those.
          </footer>

          {route.image_png_base64 && (
            <details className="route-overview">
              <summary>Full route diagram</summary>
              <div className="route-overview-img">
                <img
                  src={`data:image/png;base64,${route.image_png_base64}`}
                  alt={`Route ${selected + 1} overview`}
                />
              </div>
            </details>
          )}
        </article>
      )}
    </>
  )
}

/* ---------------- molecular search ---------------- */

export type SearchOutcome = {
  mode: 'exact' | 'similarity' | 'substructure'
  rows: (Molecule & { tanimoto?: number })[]
  note: string
  /** Set when the library legitimately holds no match, so "empty" reads as an
   *  answer rather than a failure. */
  miss?: string
}

export function SearchResults({ outcome }: { outcome: SearchOutcome }) {
  if (outcome.miss) {
    return (
      <div className="empty-state">
        <AlertIcon />
        <h3>Not in this library</h3>
        <p>{outcome.miss}</p>
        <ul>
          <li>The library is 2,269 ChEMBL approved small-molecule drugs, not a full compound catalogue</li>
          <li>Try <strong>Similarity</strong> instead — it ranks the nearest drugs even when the exact structure is absent</li>
        </ul>
      </div>
    )
  }

  if (!outcome.rows.length) {
    return (
      <div className="empty-state">
        <AlertIcon />
        <h3>No matches</h3>
        <p>{outcome.note}</p>
      </div>
    )
  }

  const showScore = outcome.rows.some((r) => r.tanimoto != null)
  return (
    <>
      <p className="result-note">{outcome.note}</p>
      <table className="results">
        <thead>
          <tr>
            <th>Structure</th>
            <th>ID</th>
            <th>Canonical SMILES</th>
            <th>MW</th>
            {showScore && <th>Tanimoto</th>}
          </tr>
        </thead>
        <tbody>
          {outcome.rows.map((row) => (
            <tr key={row.id}>
              <td>
                <div className="thumb">
                  <Depiction smiles={row.canonical_smiles} />
                </div>
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
              {showScore && (
                <td className="score">{row.tanimoto?.toFixed(4) ?? '—'}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

/* ---------------- properties ---------------- */

export function PropertyResults({ prediction }: { prediction: Prediction }) {
  const interval = prediction.prediction_interval
  const applicability = prediction.applicability
  const span = interval.upper - interval.lower
  const axisMin = interval.lower - span * 0.2
  const axisMax = interval.upper + span * 0.2
  const pct = (x: number) => ((x - axisMin) / (axisMax - axisMin)) * 100

  return (
    <div className="result-card">
      <div className="prediction-headline">
        <span className="prediction-value">{prediction.predicted_value.toFixed(2)}</span>
        <span className="prediction-units">{prediction.units}</span>
      </div>

      <div className="interval">
        <div className="interval-track">
          <div
            className="interval-span"
            style={{
              left: `${pct(interval.lower)}%`,
              width: `${pct(interval.upper) - pct(interval.lower)}%`,
            }}
          />
          <div
            className="interval-point"
            style={{ left: `${pct(prediction.predicted_value)}%` }}
          />
        </div>
        <div className="interval-labels">
          <span>{interval.lower.toFixed(2)}</span>
          <span className="interval-value">
            {prediction.predicted_value.toFixed(2)} {prediction.units}
          </span>
          <span>{interval.upper.toFixed(2)}</span>
        </div>
      </div>

      <dl className="facts">
        <dt>Prediction interval</dt>
        <dd>
          {interval.lower.toFixed(2)} to {interval.upper.toFixed(2)} {prediction.units}
          <span className="muted"> (width {span.toFixed(2)})</span>
        </dd>

        <dt>Measured coverage</dt>
        <dd>
          <strong>{(interval.empirical_coverage * 100).toFixed(0)}%</strong>
          {interval.empirical_coverage_ci_95.length === 2 && (
            <span className="muted">
              {' '}
              (95% CI {(interval.empirical_coverage_ci_95[0] * 100).toFixed(0)}–
              {(interval.empirical_coverage_ci_95[1] * 100).toFixed(0)}%, n ={' '}
              {interval.n_test})
            </span>
          )}
          <div className="muted small">
            Nominal label is {(interval.nominal_coverage * 100).toFixed(0)}%. Coverage
            is marginal across the test set — not a confidence for this molecule.
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
            ({applicability.structurally_familiar ? 'familiar' : 'novel'} vs threshold{' '}
            {applicability.threshold})
          </span>
          <div className="muted small">
            Does <em>not</em> predict accuracy — measured and found unrelated to error.
          </div>
        </dd>

        <dt>Model</dt>
        <dd className="muted">
          {prediction.model_used} · test RMSE{' '}
          {prediction.model_performance.test_rmse.toFixed(2)} · R²{' '}
          {prediction.model_performance.test_r2.toFixed(2)} ·{' '}
          {prediction.model_performance.split} split · {interval.method}
        </dd>
      </dl>
    </div>
  )
}

/* ---------------- structure ---------------- */

export function StructureResults({ representation }: { representation: Representation }) {
  const stripped =
    representation.canonical_smiles !== representation.smiles_as_given_canonical

  return (
    <div className="result-card structure-result">
      <div className="depiction">
        <img
          src={`data:image/png;base64,${representation.image_png_base64}`}
          alt={`Structure of ${representation.canonical_smiles}`}
        />
      </div>
      <dl className="facts">
        <dt>Canonical SMILES</dt>
        <dd className="mono">{representation.canonical_smiles}</dd>

        {stripped && (
          <>
            <dt>As given (not parent-stripped)</dt>
            <dd className="mono">
              {representation.smiles_as_given_canonical}
              <span className="muted"> — salt/charge stripped for the canonical form</span>
            </dd>
          </>
        )}

        <dt>InChIKey</dt>
        <dd className="mono">{representation.inchikey}</dd>

        <dt>Molecular weight</dt>
        <dd>{representation.molecular_weight.toFixed(3)}</dd>

        <dt>Morgan fingerprint</dt>
        <dd>
          radius {representation.morgan_radius}, {representation.morgan_n_bits} bits,{' '}
          {representation.morgan_fingerprint_on_bits.length} bits set
        </dd>
      </dl>
    </div>
  )
}
