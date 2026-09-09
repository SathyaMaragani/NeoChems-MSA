import { useEffect, useMemo, useRef, useState } from 'react'
import MoleculeEditor from '../components/MoleculeEditor'
import { Depiction, MoleculeCard } from '../components/MoleculeCard'
import { Collapsible, WorkspaceHeader } from '../components/Shell'
import { errorMessage } from '../components/Feedback'
import {
  AlertIcon,
  ArrowRightIcon,
  CheckIcon,
  ResetIcon,
  SparkIcon,
} from '../components/icons'
import { planRoutes, type Plan, type Reaction, type Route, type RouteNode } from '../api'

const MAX_ITERATIONS = 500

const EXAMPLES: [string, string][] = [
  ['Aspirin', 'CC(=O)Oc1ccccc1C(=O)O'],
  ['Ibuprofen', 'CC(C)Cc1ccc(C(C)C(=O)O)cc1'],
  ['Paracetamol', 'CC(=O)Nc1ccc(O)cc1'],
  ['Naproxen', 'COc1ccc2cc(C(C)C(=O)O)ccc2c1'],
]

/** One workspace, one state. Never editor-and-results at once. */
type Mode = 'editor' | 'loading' | 'results'

type Completed = {
  targetSmiles: string
  plan: Plan
  iterationLimit: number
}

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
    <section className="rstep">
      <div className="rstep-label">
        Step {index + 1} of {total}
      </div>
      <div className="rstep-flow">
        <div className="rstep-group">
          {reaction.reactants.map((reactant) => (
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
            template {reaction.template_used ?? '—'}
            {reaction.score != null && ` · policy ${reaction.score.toFixed(4)}`}
          </span>
        </div>

        <div className="rstep-group">
          <MoleculeCard smiles={product.molecule_smiles} role="Product" large />
        </div>
      </div>

      <details className="rstep-detail">
        <summary>Reaction detail</summary>
        <dl>
          <dt>Reaction SMILES</dt>
          <dd>
            <code>{reaction.reaction_smiles}</code>
          </dd>
          {reaction.template_smarts && (
            <>
              <dt>Template SMARTS</dt>
              <dd>
                <code>{reaction.template_smarts}</code>
              </dd>
            </>
          )}
        </dl>
      </details>
    </section>
  )
}

export default function RetrosynthesisWorkspace({
  smiles,
  onSmilesChange,
  backendUp,
}: {
  smiles: string
  onSmilesChange: (value: string) => void
  backendUp: boolean | null
}) {
  const [mode, setMode] = useState<Mode>('editor')
  const [completed, setCompleted] = useState<Completed | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [elapsed, setElapsed] = useState(0)
  const [iterationLimit, setIterationLimit] = useState(100)
  const [topN, setTopN] = useState(5)
  const [selected, setSelected] = useState(0)
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const timer = useRef<number | null>(null)
  const running = useRef(false)

  const trimmed = smiles.trim()
  // Results belong to the structure they were computed from. If the molecule has
  // moved on, say so rather than letting old routes read as current.
  const dirty = completed != null && completed.targetSmiles !== trimmed

  useEffect(() => {
    if (mode === 'loading') {
      setElapsed(0)
      timer.current = window.setInterval(() => setElapsed((n) => n + 1), 1000)
    } else if (timer.current) {
      clearInterval(timer.current)
      timer.current = null
    }
    return () => {
      if (timer.current) clearInterval(timer.current)
    }
  }, [mode])

  const plan = async () => {
    if (running.current || !trimmed) return
    running.current = true
    setMode('loading')
    setError(null)
    try {
      const result = await planRoutes(trimmed, topN, iterationLimit)
      setCompleted({ targetSmiles: trimmed, plan: result, iterationLimit })
      setSelected(0)
      setMode('results')
    } catch (err) {
      setError(err)
      setMode('editor')
    } finally {
      running.current = false
    }
  }

  const editMolecule = () => {
    setError(null)
    setMode('editor')
  }

  const backToResults = () => {
    if (dirty) {
      setConfirmDiscard(true)
      return
    }
    setMode('results')
  }

  const route = completed?.plan.routes[selected]
  const steps = useMemo(() => (route ? toSteps(route.tree) : []), [route])

  const canPlan = Boolean(trimmed) && backendUp !== false

  /* ---------- editor ---------- */

  const editorActions = (
    <>
      {completed && (
        <button
          className="ghost"
          onClick={backToResults}
          title={
            dirty
              ? 'Previous results were computed for a different structure'
              : undefined
          }
        >
          Back to results{dirty ? ' ●' : ''}
        </button>
      )}
      {trimmed && (
        <button className="ghost" onClick={() => onSmilesChange('')}>
          <ResetIcon /> Clear
        </button>
      )}
      <button
        className="primary lg"
        onClick={plan}
        disabled={!canPlan}
        title={
          !trimmed
            ? 'Draw or enter a target molecule first.'
            : backendUp === false
              ? 'Backend is offline.'
              : undefined
        }
      >
        <SparkIcon />
        {dirty ? 'Plan new retrosynthesis' : 'Plan retrosynthesis'}
      </button>
    </>
  )

  /* ---------- results ---------- */

  const resultsActions = completed && (
    <>
      <button className="ghost" onClick={editMolecule}>
        Edit molecule ✎
      </button>
      <button
        className="ghost"
        onClick={() => {
          const blob = new Blob([JSON.stringify(completed.plan, null, 2)], {
            type: 'application/json',
          })
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          a.download = `retrosynthesis-${Date.now()}.json`
          a.click()
          URL.revokeObjectURL(url)
        }}
      >
        Export JSON
      </button>
    </>
  )

  return (
    <div className="workspace">
      {/* The editor is never unmounted - Ketcher boots a WASM service, so
          remounting would make "Edit molecule" feel like restarting the app.
          It is hidden instead, which keeps the drawing and the zoom state. */}
      <div className="stage" hidden={mode !== 'editor'}>
        <WorkspaceHeader
          title="Retrosynthesis"
          subtitle="Design a synthetic route back from your target molecule."
          actions={editorActions}
          meta={
            dirty ? (
              <div className="inline-note warn">
                <AlertIcon />
                <span>
                  <strong>Target molecule modified.</strong> The previous results
                  were computed for a different structure — run the search again to
                  refresh them.
                </span>
              </div>
            ) : completed ? (
              <div className="inline-note">
                <CheckIcon />
                <span>
                  Editing the target from your last search. Change the structure and
                  plan again to generate new routes.
                </span>
              </div>
            ) : null
          }
        />

        {error != null && (
          <div className="banner error">
            <AlertIcon />
            <div>
              <strong>Search failed</strong>
              <span>{errorMessage(error)}</span>
            </div>
          </div>
        )}

        <div className="editor-stage">
          <div className="editor-stage-bar">
            <span className="eyebrow">Structure</span>
            <span className={`validity ${trimmed ? 'ok' : 'idle'}`}>
              {trimmed ? '✓ Structure ready' : 'Draw or paste a molecule'}
            </span>
          </div>

          <MoleculeEditor smiles={smiles} onSmilesChange={onSmilesChange} />

          <div className="editor-inputs">
            <label className="field">
              <span className="eyebrow">SMILES</span>
              <input
                className="smiles-input"
                value={smiles}
                spellCheck={false}
                placeholder="Paste a SMILES, or draw above"
                onChange={(event) => onSmilesChange(event.target.value)}
              />
            </label>

            <div className="quick-add">
              <span className="eyebrow">Quick start</span>
              <div className="quick-add-chips">
                {EXAMPLES.map(([name, value]) => (
                  <button key={name} className="chip" onClick={() => onSmilesChange(value)}>
                    {name}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <Collapsible title="Advanced retrosynthesis settings">
            <div className="settings-grid">
              <label>
                <span>Search depth</span>
                <input
                  type="range"
                  min={20}
                  max={MAX_ITERATIONS}
                  step={20}
                  value={iterationLimit}
                  onChange={(e) => setIterationLimit(Number(e.target.value))}
                />
                <em>
                  {iterationLimit} iterations ·{' '}
                  {iterationLimit > 250 ? 'up to ~95s' : 'up to ~20s'}
                </em>
              </label>
              <label>
                <span>Maximum routes</span>
                <input
                  type="number"
                  min={1}
                  max={25}
                  value={topN}
                  onChange={(e) => setTopN(Number(e.target.value))}
                />
                <em>Only solved routes are returned.</em>
              </label>
              <label className="readonly">
                <span>Reaction database</span>
                <input value="USPTO (only database loaded)" readOnly />
                <em>Templates derived from US patent reactions.</em>
              </label>
            </div>
            <p className="settings-note">
              Starting-material, synthetic-accessibility and rare-reagent constraints
              are not exposed by this build&apos;s API, so they are not shown here
              rather than shown as controls that do nothing.
            </p>
          </Collapsible>
        </div>
      </div>

      {/* ---------- loading ---------- */}
      {mode === 'loading' && (
        <div className="stage loading-stage">
          <div className="loading-mark">
            <SparkIcon />
          </div>
          <h2>Planning retrosynthesis</h2>
          <p>Exploring reaction space with the USPTO expansion policy.</p>
          <div className="loading-bar">
            <span />
          </div>
          <p className="loading-meta">
            {elapsed}s elapsed · {iterationLimit} iterations requested ·{' '}
            {iterationLimit > 250 ? 'typically ~95s' : 'typically under 20s'}
          </p>
          <p className="loading-note">
            The tree search runs on CPU and reports no intermediate progress, so
            there is no percentage to show.
          </p>
        </div>
      )}

      {/* ---------- results ---------- */}
      {mode === 'results' && completed && (
        <div className="stage">
          <WorkspaceHeader
            title="Retrosynthesis results"
            actions={resultsActions}
            meta={
              <div className="target-strip">
                <div className="target-thumb">
                  <Depiction smiles={completed.targetSmiles} alt="Target molecule" />
                </div>
                <div className="target-meta">
                  <span className="eyebrow">Target molecule</span>
                  <code>{completed.targetSmiles}</code>
                  <span className="target-summary">
                    {completed.plan.iterations_used} iterations ·{' '}
                    {completed.plan.search_time_seconds}s ·{' '}
                    {completed.plan.solved_routes_found} solved route
                    {completed.plan.solved_routes_found === 1 ? '' : 's'} found
                  </span>
                </div>
              </div>
            }
          />

          {dirty && (
            <div className="banner warn">
              <AlertIcon />
              <div>
                <strong>These results are out of date</strong>
                <span>
                  The molecule in the editor has changed since this search ran. Plan
                  again to get routes for the current structure.
                </span>
              </div>
            </div>
          )}

          {!completed.plan.is_solved ? (
            <div className="empty-state">
              <AlertIcon />
              <h3>No feasible route found</h3>
              <p>
                Nothing solved in {completed.plan.iterations_used} iterations (
                {completed.plan.search_time_seconds}s). Only routes that bottom out
                in purchasable material are returned, so an unsolved search returns
                nothing rather than a partial guess.
              </p>
              <ul>
                <li>
                  Increase search depth — currently {completed.iterationLimit}, max{' '}
                  {MAX_ITERATIONS}
                </li>
                <li>Try a simpler or more common target</li>
                <li>
                  This target may sit outside USPTO-derived chemistry altogether
                </li>
              </ul>
              <div className="empty-actions">
                <button className="primary" onClick={editMolecule}>
                  Edit molecule ✎
                </button>
                <button
                  className="ghost"
                  onClick={() => {
                    setIterationLimit(MAX_ITERATIONS)
                    editMolecule()
                  }}
                >
                  Raise depth to {MAX_ITERATIONS} and edit
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="route-rail">
                {completed.plan.routes.map((r: Route, index: number) => (
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
                            ? Math.min(
                                ...steps.map((s) => s.reaction.score ?? 1),
                              ).toFixed(4)
                            : '—'}
                        </dd>
                      </div>
                    </dl>
                  </header>

                  <div className="route-steps-full">
                    {steps.map((step, index) => (
                      <Step
                        key={`${step.reaction.reaction_smiles}-${index}`}
                        reaction={step.reaction}
                        product={step.product}
                        index={index}
                        total={steps.length}
                      />
                    ))}
                  </div>

                  <footer className="route-footnote">
                    State score measures how completely the route bottoms out in
                    purchasable material, and policy is the expansion model&apos;s
                    probability for that template. Neither is a yield, cost or
                    feasibility prediction — this build has no model for those.
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
          )}
        </div>
      )}

      {confirmDiscard && (
        <div className="modal-backdrop" onClick={() => setConfirmDiscard(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Discard molecule changes?</h3>
            <p>
              Your edited structure has not been used to generate a new
              retrosynthesis result. Going back will show routes for the previous
              structure.
            </p>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setConfirmDiscard(false)}>
                Continue editing
              </button>
              <button
                className="primary"
                onClick={() => {
                  setConfirmDiscard(false)
                  if (completed) onSmilesChange(completed.targetSmiles)
                  setMode('results')
                }}
              >
                Discard changes
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
