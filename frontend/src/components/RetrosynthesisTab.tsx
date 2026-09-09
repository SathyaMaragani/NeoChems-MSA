import { useEffect, useRef, useState } from 'react'
import { planRoutes, type Plan, type Reaction, type Route, type RouteNode } from '../api'
import { EmptySmilesNotice, ErrorBox } from './Feedback'
import { MoleculeCard } from './MoleculeCard'
import { AlertIcon, ArrowRightIcon, CheckIcon, ChevronIcon, InfoIcon } from './icons'

const DEFAULT_ITERATIONS = 100
const MAX_ITERATIONS = 500

/** Flatten the route tree into steps, deepest first — the order you'd run them. */
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

function Step({
  reaction,
  product,
  index,
  total,
}: {
  reaction: Reaction
  product: RouteNode
  index: number
  total: number
}) {
  return (
    <div className="step">
      <div className="step-flow">
        <div className="step-reactants">
          {reaction.reactants.map((reactant) => (
            <MoleculeCard
              key={reactant.molecule_smiles}
              smiles={reactant.molecule_smiles}
              role="Reactant"
              inStock={reactant.is_stock_available}
            />
          ))}
        </div>

        <div className="step-arrow">
          <span className="step-arrow-label">
            step {index + 1}/{total}
          </span>
          <ArrowRightIcon />
          {reaction.score != null && (
            <span className="step-arrow-meta">policy {reaction.score.toFixed(3)}</span>
          )}
        </div>

        <div className="step-product">
          <MoleculeCard smiles={product.molecule_smiles} role="Product" />
        </div>
      </div>

      <details className="step-detail">
        <summary>Reaction detail</summary>
        <dl>
          <dt>Reaction SMILES</dt>
          <dd>
            <code>{reaction.reaction_smiles}</code>
          </dd>
          <dt>Template</dt>
          <dd>{reaction.template_used ?? '—'}</dd>
          {reaction.template_smarts && (
            <>
              <dt>Template SMARTS</dt>
              <dd>
                <code>{reaction.template_smarts}</code>
              </dd>
            </>
          )}
          <dt>Policy probability</dt>
          <dd>{reaction.score != null ? reaction.score.toFixed(4) : '—'}</dd>
        </dl>
      </details>
    </div>
  )
}

function RouteInfo({ route, plan }: { route: Route; plan: Plan }) {
  const steps = toSteps(route.tree)
  const leaves: RouteNode[] = []
  const collect = (node: RouteNode) => {
    if (!node.reactions.length) leaves.push(node)
    node.reactions.forEach((r) => r.reactants.forEach(collect))
  }
  collect(route.tree)
  const policies = steps
    .map((s) => s.reaction.score)
    .filter((s): s is number => s != null)
  const weakest = policies.length ? Math.min(...policies) : null

  return (
    <aside className="route-info">
      <h4>Route information</h4>
      <dl>
        <div>
          <dt>State score</dt>
          <dd className="strong">{route.state_score?.toFixed(4) ?? '—'}</dd>
        </div>
        <div>
          <dt>Steps</dt>
          <dd>{route.number_of_reactions}</dd>
        </div>
        <div>
          <dt>Starting materials</dt>
          <dd>{leaves.length} — all purchasable</dd>
        </div>
        <div>
          <dt>Weakest step policy</dt>
          <dd>{weakest != null ? weakest.toFixed(4) : '—'}</dd>
        </div>
        <div>
          <dt>Search</dt>
          <dd>
            {plan.iterations_used} iters · {plan.search_time_seconds}s
          </dd>
        </div>
      </dl>
      <p className="route-info-note">
        <InfoIcon />
        <span>
          State score measures how completely the route bottoms out in purchasable
          material. It is a search heuristic — <strong>not</strong> a yield,
          cost or feasibility prediction.
        </span>
      </p>
    </aside>
  )
}

export default function RetrosynthesisTab({ smiles }: { smiles: string }) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [iterationLimit, setIterationLimit] = useState(DEFAULT_ITERATIONS)
  const [topN, setTopN] = useState(5)
  const [selected, setSelected] = useState(0)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    if (busy) {
      setElapsed(0)
      timer.current = window.setInterval(() => setElapsed((n) => n + 1), 1000)
    } else if (timer.current) {
      clearInterval(timer.current)
      timer.current = null
    }
    return () => {
      if (timer.current) clearInterval(timer.current)
    }
  }, [busy])

  const run = async () => {
    setBusy(true)
    setError(null)
    setPlan(null)
    setSelected(0)
    try {
      setPlan(await planRoutes(smiles, topN, iterationLimit))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const expected = iterationLimit > 250 ? '~95s' : 'up to ~20s'
  const route = plan?.routes[selected]

  return (
    <div className="workspace">
      <div className="workspace-controls">
        <button className="primary lg" onClick={run} disabled={busy || !smiles.trim()}>
          {busy ? `Searching… ${elapsed}s` : 'Plan retrosynthesis'}
        </button>
        <label>
          Search depth
          <input
            type="range"
            min={20}
            max={MAX_ITERATIONS}
            step={20}
            value={iterationLimit}
            onChange={(e) => setIterationLimit(Number(e.target.value))}
          />
          <span className="range-value">
            {iterationLimit} iterations · {expected}
          </span>
        </label>
        <label className="narrow">
          Routes
          <input
            type="number"
            min={1}
            max={25}
            value={topN}
            onChange={(e) => setTopN(Number(e.target.value))}
          />
        </label>
      </div>

      {!smiles.trim() && <EmptySmilesNotice />}

      {busy && (
        <div className="banner info">
          <span className="spinner" />
          <div>
            <strong>Searching — {elapsed}s elapsed</strong>
            <span>
              {iterationLimit} iterations, expected {expected}. The tree search is
              CPU-bound; the page is not frozen.
            </span>
          </div>
        </div>
      )}

      <ErrorBox error={error} />

      {plan && !plan.is_solved && (
        <div className="banner warn">
          <AlertIcon />
          <div>
            <strong>No route found</strong>
            <span>
              Nothing solved in {plan.iterations_used} iterations ({plan.search_time_seconds}s).
              {plan.iteration_limit < MAX_ITERATIONS
                ? ` Raise search depth — currently ${plan.iteration_limit}, max ${MAX_ITERATIONS}.`
                : ` Already at the maximum of ${MAX_ITERATIONS}; this target may sit outside USPTO-derived chemistry.`}
            </span>
          </div>
        </div>
      )}

      {plan && plan.is_solved && route && (
        <>
          <div className="banner success">
            <CheckIcon />
            <div>
              <strong>Retrosynthesis complete</strong>
              <span>
                {plan.iterations_used} iterations in {plan.search_time_seconds}s ·{' '}
                {plan.solved_routes_found} solved route
                {plan.solved_routes_found === 1 ? '' : 's'} found · showing{' '}
                {plan.routes_returned}
              </span>
            </div>
          </div>

          <div className="route-chips">
            {plan.routes.map((r, index) => (
              <button
                key={r.route_id}
                className={`route-chip${index === selected ? ' active' : ''}`}
                onClick={() => setSelected(index)}
              >
                <span className="route-chip-title">Route {index + 1}</span>
                <span className="route-chip-score">
                  {r.number_of_reactions} step{r.number_of_reactions === 1 ? '' : 's'} ·{' '}
                  {r.state_score?.toFixed(4)}
                </span>
              </button>
            ))}
          </div>

          <article className="route-card">
            <header className="route-card-head">
              <div>
                <h3>Route {selected + 1}</h3>
                {selected === 0 && <span className="badge best">Highest score</span>}
                {selected > 0 && <span className="badge alt">Alternative</span>}
              </div>
              <span className="muted">
                {route.number_of_reactions} step
                {route.number_of_reactions === 1 ? '' : 's'} · state score{' '}
                {route.state_score?.toFixed(4)}
              </span>
            </header>

            <div className="route-card-body">
              <div className="route-steps">
                {toSteps(route.tree).map((step, index, all) => (
                  <Step
                    key={`${step.reaction.reaction_smiles}-${index}`}
                    reaction={step.reaction}
                    product={step.product}
                    index={index}
                    total={all.length}
                  />
                ))}
              </div>
              <RouteInfo route={route} plan={plan} />
            </div>

            {route.image_png_base64 && (
              <details className="route-overview">
                <summary>
                  Full route diagram <ChevronIcon />
                </summary>
                <div className="route-overview-img">
                  <img
                    src={`data:image/png;base64,${route.image_png_base64}`}
                    alt={`Route ${selected + 1} overview`}
                  />
                </div>
              </details>
            )}
          </article>
        </>
      )}
    </div>
  )
}
