import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import MoleculeEditor from './components/MoleculeEditor'
import { BrandMark } from './components/BrandMark'
import { Collapsible, Sidebar, TopBar, WorkspaceHeader } from './components/Shell'
import type { BackendStatus, WorkspaceKey } from './components/Shell'
import { Depiction } from './components/MoleculeCard'
import { errorMessage } from './components/Feedback'
import { AlertIcon, CheckIcon, ResetIcon, SparkIcon } from './components/icons'
import {
  PropertyResults,
  RetroResults,
  SearchResults,
  StructureResults,
  type SearchOutcome,
} from './workspaces/results'
import {
  API_BASE,
  ApiError,
  evidenceStatus as fetchEvidenceStatus,
  exactSearch,
  listProperties,
  moleculeStats,
  planRoutes,
  predictProperty,
  represent,
  resolveQuery,
  retroHealth,
  similaritySearch,
  substructureSearch,
  type EvidenceStatus,
  type Plan,
  type Prediction,
  type PropertySpec,
  type Representation,
} from './api'
import './App.css'
import './workspace.css'

const PATHS: Record<WorkspaceKey, string> = {
  retrosynthesis: '/retrosynthesis',
  search: '/search',
  properties: '/properties',
  structure: '/structure',
}

const EXAMPLES: [string, string][] = [
  ['Aspirin', 'CC(=O)Oc1ccccc1C(=O)O'],
  ['Ibuprofen', 'CC(C)Cc1ccc(C(C)C(=O)O)cc1'],
  ['Paracetamol', 'CC(=O)Nc1ccc(O)cc1'],
  ['Naproxen', 'COc1ccc2cc(C(C)C(=O)O)ccc2c1'],
]

const COPY: Record<WorkspaceKey, { title: string; subtitle: string; action: string; verb: string }> = {
  retrosynthesis: {
    title: 'Retrosynthesis',
    subtitle: 'Design a synthetic route back from your target molecule.',
    action: 'Plan retrosynthesis',
    verb: 'Planning retrosynthesis',
  },
  search: {
    title: 'Molecular search',
    subtitle: 'Find compounds in the local ChEMBL approved-drug library.',
    action: 'Search library',
    verb: 'Searching the library',
  },
  properties: {
    title: 'Properties',
    subtitle: 'Predict aqueous solubility with a calibrated prediction interval.',
    action: 'Predict properties',
    verb: 'Predicting properties',
  },
  structure: {
    title: 'Structure',
    subtitle: 'Canonical form, identifiers and depiction — no database involved.',
    action: 'Analyse structure',
    verb: 'Analysing structure',
  },
}

type Mode = 'editor' | 'loading' | 'results'
type Outcome =
  | { kind: 'retrosynthesis'; data: Plan }
  | { kind: 'search'; data: SearchOutcome }
  | { kind: 'properties'; data: Prediction }
  | { kind: 'structure'; data: Representation }

type Completed = { smiles: string; outcome: Outcome }

function workspaceFromPath(path: string): WorkspaceKey {
  const found = (Object.keys(PATHS) as WorkspaceKey[]).find((key) =>
    path.startsWith(PATHS[key]),
  )
  return found ?? 'retrosynthesis'
}

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceKey>(() =>
    workspaceFromPath(window.location.pathname),
  )
  const [smiles, setSmiles] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [mode, setMode] = useState<Mode>('editor')
  // Results are kept per workspace, so switching tabs does not throw away work.
  const [completed, setCompleted] = useState<Partial<Record<WorkspaceKey, Completed>>>({})
  const [error, setError] = useState<unknown>(null)
  const [elapsed, setElapsed] = useState(0)
  const [resolving, setResolving] = useState(false)
  const [resolvedNote, setResolvedNote] = useState<string | null>(null)
  const [status, setStatus] = useState<BackendStatus>({
    up: null,
    moleculeCount: null,
    modelLoadSeconds: null,
  })

  // per-workspace options
  const [iterationLimit, setIterationLimit] = useState(100)
  const [topRoutes, setTopRoutes] = useState(10)
  const [includeConditions, setIncludeConditions] = useState(true)
  const [evidence, setEvidence] = useState<EvidenceStatus | null>(null)
  const [searchMode, setSearchMode] = useState<SearchOutcome['mode']>('similarity')
  const [topN, setTopN] = useState(10)
  const [minSimilarity, setMinSimilarity] = useState(0)
  const [properties, setProperties] = useState<PropertySpec[] | null>(null)
  const [model, setModel] = useState('')
  const [alpha, setAlpha] = useState(0.1)

  const timer = useRef<number | null>(null)
  const running = useRef(false)

  const trimmed = smiles.trim()
  const current = completed[workspace]
  const dirty = current != null && current.smiles !== trimmed
  const copy = COPY[workspace]

  /* ---------- backend status ---------- */

  useEffect(() => {
    let cancelled = false
    const check = () =>
      retroHealth()
        .then((body) => {
          if (!cancelled)
            setStatus((s) => ({ ...s, up: true, modelLoadSeconds: body.load_time_seconds }))
        })
        .catch(() => !cancelled && setStatus((s) => ({ ...s, up: false })))
    check()
    const t = setInterval(check, 15_000)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  useEffect(() => {
    if (!status.up || status.moleculeCount !== null) return
    moleculeStats()
      .then((body) => setStatus((s) => ({ ...s, moleculeCount: body.total })))
      .catch(() => undefined)
  }, [status.up, status.moleculeCount])

  //  Fetched once so the UI can name the corpus that is actually searched and
  //  its size, instead of letting "evidence" read as "the literature".
  useEffect(() => {
    if (!status.up || evidence) return
    fetchEvidenceStatus()
      .then(setEvidence)
      .catch(() => undefined)
  }, [status.up, evidence])

  useEffect(() => {
    if (!status.up || properties) return
    listProperties()
      .then((body) => {
        setProperties(body.properties)
        const first = body.properties[0]
        if (first) {
          setModel(first.default_model)
          if (!first.calibrated_alphas.includes(alpha)) setAlpha(first.calibrated_alphas[0])
        }
      })
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.up, properties])

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

  /* ---------- routing ---------- */

  const navigate = useCallback(
    (key: WorkspaceKey) => {
      setWorkspace(key)
      setError(null)
      // Show that workspace's own result if it has one; otherwise the editor.
      setMode(completed[key] ? 'results' : 'editor')
      if (window.location.pathname !== PATHS[key]) {
        window.history.pushState({ workspace: key }, '', PATHS[key])
      }
    },
    [completed],
  )

  useEffect(() => {
    const onPop = () => setWorkspace(workspaceFromPath(window.location.pathname))
    window.addEventListener('popstate', onPop)
    if (window.location.pathname === '/') {
      window.history.replaceState({ workspace }, '', PATHS[workspace])
    }
    return () => window.removeEventListener('popstate', onPop)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ---------- input: names as well as SMILES ---------- */

  const load = useCallback(async (text: string) => {
    const value = text.trim()
    setResolvedNote(null)
    if (!value) {
      setSmiles('')
      return
    }
    setResolving(true)
    setError(null)
    try {
      const resolution = await resolveQuery(value)
      setSmiles(resolution.canonical_smiles)
      if (resolution.source === 'pubchem') {
        setResolvedNote(`Resolved "${value}" to ${resolution.matched_name} via PubChem`)
      }
    } catch (err) {
      setError(err)
    } finally {
      setResolving(false)
    }
  }, [])

  /* ---------- run ---------- */

  const run = async () => {
    if (running.current || !trimmed) return
    running.current = true
    setMode('loading')
    setError(null)
    try {
      let outcome: Outcome
      if (workspace === 'retrosynthesis') {
        outcome = {
          kind: 'retrosynthesis',
          data: await planRoutes(trimmed, topRoutes, iterationLimit, includeConditions),
        }
      } else if (workspace === 'search') {
        outcome = { kind: 'search', data: await runSearch() }
      } else if (workspace === 'properties') {
        const spec = properties?.[0]
        outcome = {
          kind: 'properties',
          data: await predictProperty(trimmed, spec?.property ?? 'solubility', model || undefined, alpha),
        }
      } else {
        outcome = { kind: 'structure', data: await represent(trimmed) }
      }
      setCompleted((prev) => ({ ...prev, [workspace]: { smiles: trimmed, outcome } }))
      setMode('results')
    } catch (err) {
      setError(err)
      setMode('editor')
    } finally {
      running.current = false
    }
  }

  const runSearch = async (): Promise<SearchOutcome> => {
    if (searchMode === 'exact') {
      try {
        const match = await exactSearch(trimmed)
        return { mode: 'exact', rows: [match], note: 'Exact match on canonical SMILES or InChIKey.' }
      } catch (err) {
        // 404 is a real answer here, not a failure: the library is approved drugs
        // only, so most structures legitimately are not in it.
        if (err instanceof ApiError && err.status === 404) {
          return {
            mode: 'exact',
            rows: [],
            note: '',
            miss: `No approved drug in the library has this exact structure.`,
          }
        }
        throw err
      }
    }
    if (searchMode === 'similarity') {
      const body = await similaritySearch(trimmed, topN, minSimilarity)
      return {
        mode: 'similarity',
        rows: body.results,
        note: `Tanimoto over Morgan fingerprints against ${status.moleculeCount?.toLocaleString() ?? 'the'} compounds. Query canonicalised to ${body.query_canonical_smiles}.`,
      }
    }
    const body = await substructureSearch(trimmed, topN)
    return {
      mode: 'substructure',
      rows: body.results,
      note: `${body.count} compound${body.count === 1 ? '' : 's'} contain this substructure.`,
    }
  }

  /* ---------- options per workspace ---------- */

  const options: ReactNode =
    workspace === 'retrosynthesis' ? (
      <div className="settings-grid">
        <label>
          <span>Search depth</span>
          <input
            type="range"
            min={20}
            max={500}
            step={20}
            value={iterationLimit}
            onChange={(e) => setIterationLimit(Number(e.target.value))}
          />
          <em>
            {iterationLimit} iterations · {iterationLimit > 250 ? 'up to ~95s' : 'up to ~20s'}
          </em>
        </label>
        <label>
          <span>Maximum routes</span>
          <input type="number" min={1} max={25} value={topRoutes} onChange={(e) => setTopRoutes(Number(e.target.value))} />
          <em>Only solved routes are returned.</em>
        </label>
        <label className="readonly">
          <span>Reaction database</span>
          <input value="USPTO (only database loaded)" readOnly />
          <em>Templates derived from US patent reactions.</em>
        </label>
        <label className="checkbox">
          <span>Experimental evidence</span>
          <input
            type="checkbox"
            checked={includeConditions}
            onChange={(e) => setIncludeConditions(e.target.checked)}
          />
          <em>
            {evidence?.available
              ? `Source: ${evidence.provider_display_name}. Coverage: ${
                  evidence.indexed_reactions?.toLocaleString() ?? 'unknown'
                } indexed reactions — a finite corpus, not a search of the literature.`
              : 'No evidence source is configured; every step will report no verified evidence.'}
          </em>
        </label>
      </div>
    ) : workspace === 'search' ? (
      <div className="settings-grid">
        <label>
          <span>Search type</span>
          <select value={searchMode} onChange={(e) => setSearchMode(e.target.value as SearchOutcome['mode'])}>
            <option value="similarity">Similarity (Tanimoto)</option>
            <option value="exact">Exact structure</option>
            <option value="substructure">Substructure</option>
          </select>
          <em>
            {searchMode === 'similarity' && 'Ranks nearest drugs. Stereo-blind.'}
            {searchMode === 'exact' && 'Canonical SMILES, falling back to InChIKey.'}
            {searchMode === 'substructure' && 'Finds drugs CONTAINING your structure.'}
          </em>
        </label>
        {searchMode !== 'exact' && (
          <label>
            <span>Max results</span>
            <input type="number" min={1} max={200} value={topN} onChange={(e) => setTopN(Number(e.target.value))} />
          </label>
        )}
        {searchMode === 'similarity' && (
          <label>
            <span>Min similarity</span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={minSimilarity}
              onChange={(e) => setMinSimilarity(Number(e.target.value))}
            />
          </label>
        )}
      </div>
    ) : workspace === 'properties' ? (
      <div className="settings-grid">
        <label>
          <span>Model</span>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {properties?.[0]?.models.map((m) => (
              <option key={m.model} value={m.model}>
                {m.model} (RMSE {m.test_rmse.toFixed(2)})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Confidence level</span>
          <select value={alpha} onChange={(e) => setAlpha(Number(e.target.value))}>
            {properties?.[0]?.models
              .find((m) => m.model === model)
              ?.coverage.map((row) => (
                <option key={row.alpha} value={row.alpha}>
                  α = {row.alpha} — measured {(row.empirical * 100).toFixed(0)}% coverage
                </option>
              ))}
          </select>
          <em>Labelled with measured coverage, not the nominal level.</em>
        </label>
      </div>
    ) : (
      <p className="settings-note">
        Structure analysis has no options — it canonicalises whatever is on the canvas.
      </p>
    )

  /* ---------- actions ---------- */

  const canRun = Boolean(trimmed) && status.up !== false && !resolving

  const actions = (
    <>
      {current && mode === 'editor' && (
        <button className="ghost" onClick={() => setMode('results')} title={dirty ? 'Results were computed for a different structure' : undefined}>
          Back to results{dirty ? ' ●' : ''}
        </button>
      )}
      {mode === 'results' && (
        <button className="ghost" onClick={() => setMode('editor')}>
          Edit molecule ✎
        </button>
      )}
      {mode === 'editor' && trimmed && (
        <button className="ghost" onClick={() => load('')}>
          <ResetIcon /> Clear
        </button>
      )}
      <button
        className="primary lg"
        onClick={run}
        disabled={!canRun}
        title={!trimmed ? 'Draw or enter a molecule first.' : status.up === false ? 'Backend is offline.' : undefined}
      >
        <SparkIcon />
        {dirty ? `Re-run · ${copy.action}` : copy.action}
      </button>
    </>
  )

  return (
    <div className="app">
      <Sidebar
        active={workspace}
        onSelect={navigate}
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((v) => !v)}
      />

      <div className="main">
        <TopBar status={status} onLoadSmiles={load} />

        {status.up === false && (
          <div className="offline-bar">
            Backend unreachable on <code>{API_BASE}</code>. From the repo root:{' '}
            <code>docker compose up -d</code> then <code>uvicorn backend.api.main:app --port 8434</code>.
          </div>
        )}

        <main className="workspace-root">
          <div className="workspace">
            {/* One editor for every workspace. Never unmounted - Ketcher boots a
                WASM service, so remounting would cost seconds on each switch. */}
            <div className="stage" hidden={mode !== 'editor'}>
              <WorkspaceHeader
                title={copy.title}
                subtitle={copy.subtitle}
                actions={actions}
                meta={
                  dirty ? (
                    <div className="inline-note warn">
                      <AlertIcon />
                      <span>
                        <strong>Molecule modified.</strong> The results on this tab were
                        computed for a different structure — run again to refresh them.
                      </span>
                    </div>
                  ) : resolvedNote ? (
                    <div className="inline-note">
                      <CheckIcon />
                      <span>{resolvedNote}</span>
                    </div>
                  ) : null
                }
              />

              {error != null && (
                <div className="banner error">
                  <AlertIcon />
                  <div>
                    <strong>That didn’t work</strong>
                    <span>{errorMessage(error)}</span>
                  </div>
                </div>
              )}

              <div className="editor-stage">
                <div className="editor-stage-bar">
                  <span className="eyebrow">Structure</span>
                  <span className={`validity ${trimmed ? 'ok' : 'idle'}`}>
                    {resolving ? 'Resolving…' : trimmed ? '✓ Structure ready' : 'Draw, paste a SMILES, or type a name'}
                  </span>
                </div>

                <MoleculeEditor smiles={smiles} onSmilesChange={setSmiles} />

                <div className="editor-inputs">
                  <label className="field">
                    <span className="eyebrow">SMILES or compound name</span>
                    <input
                      className="smiles-input"
                      defaultValue={smiles}
                      key={smiles}
                      spellCheck={false}
                      placeholder="Paste a SMILES, or type a name like glucose — press Enter"
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') load(event.currentTarget.value)
                      }}
                      onBlur={(event) => {
                        if (event.currentTarget.value.trim() !== smiles.trim()) {
                          load(event.currentTarget.value)
                        }
                      }}
                    />
                  </label>

                  <div className="quick-add">
                    <span className="eyebrow">Quick start</span>
                    <div className="quick-add-chips">
                      {EXAMPLES.map(([name, value]) => (
                        <button key={name} className="chip" onClick={() => setSmiles(value)}>
                          {name}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                <Collapsible title={`${copy.title} options`}>{options}</Collapsible>
              </div>
            </div>

            {mode === 'loading' && (
              <div className="stage loading-stage">
                <div className="loading-mark">
                  <BrandMark height={44} />
                </div>
                <h2>{copy.verb}</h2>
                <p>{copy.subtitle}</p>
                <div className="loading-bar">
                  <span />
                </div>
                <p className="loading-meta">{elapsed}s elapsed</p>
                {workspace === 'retrosynthesis' && (
                  <p className="loading-note">
                    The tree search runs on CPU and reports no intermediate progress, so
                    there is no percentage to show.
                  </p>
                )}
              </div>
            )}

            {mode === 'results' && current && (
              <div className="stage">
                <WorkspaceHeader
                  title={`${copy.title} results`}
                  actions={actions}
                  meta={
                    <div className="target-strip">
                      <div className="target-thumb">
                        <Depiction smiles={current.smiles} alt="Query molecule" />
                      </div>
                      <div className="target-meta">
                        <span className="eyebrow">Query molecule</span>
                        <code>{current.smiles}</code>
                        {current.outcome.kind === 'retrosynthesis' && (
                          <span className="target-summary">
                            {current.outcome.data.iterations_used} iterations ·{' '}
                            {current.outcome.data.search_time_seconds}s ·{' '}
                            {current.outcome.data.solved_routes_found} solved route
                            {current.outcome.data.solved_routes_found === 1 ? '' : 's'}
                          </span>
                        )}
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
                        The molecule has changed since this ran. Use{' '}
                        <strong>Re-run · {copy.action}</strong> to refresh them.
                      </span>
                    </div>
                  </div>
                )}

                {current.outcome.kind === 'retrosynthesis' && (
                  <RetroResults plan={current.outcome.data} evidenceStatus={evidence} />
                )}
                {current.outcome.kind === 'search' && <SearchResults outcome={current.outcome.data} />}
                {current.outcome.kind === 'properties' && <PropertyResults prediction={current.outcome.data} />}
                {current.outcome.kind === 'structure' && <StructureResults representation={current.outcome.data} />}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
