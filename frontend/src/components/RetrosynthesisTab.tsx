import { useEffect, useRef, useState } from 'react'
import { planRoutes, type Plan, type Reaction, type RouteNode } from '../api'
import { EmptySmilesNotice, ErrorBox } from './Feedback'

const DEFAULT_ITERATIONS = 100
const MAX_ITERATIONS = 500

function ReactionList({ node, depth = 0 }: { node: RouteNode; depth?: number }) {
  if (!node.reactions.length) return null
  return (
    <ul className="rxn-list">
      {node.reactions.map((rxn: Reaction, index: number) => (
        <li key={index} style={{ marginLeft: depth * 12 }}>
          <div className="rxn-smiles mono">{rxn.reaction_smiles}</div>
          <div className="rxn-meta">
            <span>
              reactants:{' '}
              {rxn.reactants.map((r) => (
                <code
                  key={r.molecule_smiles}
                  className={r.is_stock_available ? 'in-stock' : 'not-stock'}
                  title={r.is_stock_available ? 'in stock' : 'not in stock'}
                >
                  {r.molecule_smiles}
                </code>
              ))}
            </span>
            <span className="muted">
              template {rxn.template_used ?? '?'}
              {rxn.score != null && ` - policy ${rxn.score.toFixed(4)}`}
            </span>
          </div>
          {rxn.reactants.map((child) => (
            <ReactionList key={child.molecule_smiles} node={child} depth={depth + 1} />
          ))}
        </li>
      ))}
    </ul>
  )
}

export default function RetrosynthesisTab({ smiles }: { smiles: string }) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [iterationLimit, setIterationLimit] = useState(DEFAULT_ITERATIONS)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [topN, setTopN] = useState(3)
  const timer = useRef<number | null>(null)

  // A search is not a spinner-length wait: 100 iterations runs 2-20s and 500 can
  // take ~95s, so show the clock ticking rather than an opaque hourglass.
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
    try {
      setPlan(await planRoutes(smiles, topN, iterationLimit))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const expected = iterationLimit > 250 ? '~95s' : 'up to ~20s'

  return (
    <div>
      <p className="tab-intro">
        Plans retrosynthetic routes with AiZynthFinder. Only routes that bottom out
        in purchasable material are returned.
      </p>

      {!smiles.trim() && <EmptySmilesNotice />}

      <div className="controls">
        <button className="primary" onClick={run} disabled={busy || !smiles.trim()}>
          {busy ? 'Searching...' : 'Plan Synthesis'}
        </button>
        <button className="link-button" onClick={() => setShowAdvanced((v) => !v)}>
          {showAdvanced ? 'Hide advanced' : 'Advanced'}
        </button>
      </div>

      {showAdvanced && (
        <div className="advanced">
          <label>
            Iteration limit
            <input
              type="number"
              min={1}
              max={MAX_ITERATIONS}
              value={iterationLimit}
              onChange={(e) => setIterationLimit(Number(e.target.value))}
            />
          </label>
          <label>
            Routes
            <input
              type="number"
              min={1}
              max={25}
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
            />
          </label>
          <p className="muted">
            Default {DEFAULT_ITERATIONS} solves most targets in seconds. Molecules like
            ibuprofen need ~500 and take about 95 seconds. Above {MAX_ITERATIONS} the
            backend returns 400 rather than hanging.
          </p>
        </div>
      )}

      {busy && (
        <div className="notice notice-info">
          <strong>Searching - {elapsed}s elapsed</strong>
          <span>
            {iterationLimit} iterations, expected {expected}. The tree search is
            CPU-bound; this page is not frozen.
          </span>
        </div>
      )}

      <ErrorBox error={error} />

      {plan && (
        <div className="result">
          <div className={plan.is_solved ? 'verdict solved' : 'verdict unsolved'}>
            {plan.is_solved ? 'Solved' : 'No route found'}
            <span className="verdict-detail">
              {plan.iterations_used} iterations in {plan.search_time_seconds}s -{' '}
              {plan.solved_routes_found} solved route
              {plan.solved_routes_found === 1 ? '' : 's'} found
            </span>
          </div>

          {!plan.is_solved && (
            <div className="notice notice-warn">
              No route found in {plan.iterations_used} iterations.
              {plan.iteration_limit < MAX_ITERATIONS
                ? ` Try raising the iteration limit (currently ${plan.iteration_limit}, max ${MAX_ITERATIONS}) under Advanced.`
                : ` Already at the maximum of ${MAX_ITERATIONS}; this target may be outside USPTO-derived chemistry.`}
            </div>
          )}

          {plan.routes.map((route) => (
            <article key={route.route_id} className="route">
              <header>
                <strong>Route {route.route_id + 1}</strong>
                <span className="muted">
                  {route.number_of_reactions} step
                  {route.number_of_reactions === 1 ? '' : 's'}
                  {route.state_score != null &&
                    ` - state score ${route.state_score.toFixed(4)}`}
                </span>
              </header>
              {route.image_png_base64 && (
                <div className="route-image">
                  <img
                    src={`data:image/png;base64,${route.image_png_base64}`}
                    alt={`Route ${route.route_id + 1}`}
                  />
                </div>
              )}
              <ReactionList node={route.tree} />
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
