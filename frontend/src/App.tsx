import { useCallback, useEffect, useState } from 'react'
import QsarTab from './components/QsarTab'
import RepresentTab from './components/RepresentTab'
import SearchTab from './components/SearchTab'
import { Sidebar, TopBar, WorkspaceHeader, type BackendStatus, type WorkspaceKey } from './components/Shell'
import MoleculeBar from './workspaces/MoleculeBar'
import RetrosynthesisWorkspace from './workspaces/RetrosynthesisWorkspace'
import { moleculeStats, retroHealth } from './api'
import './App.css'
import './workspace.css'

const PATHS: Record<WorkspaceKey, string> = {
  retrosynthesis: '/retrosynthesis',
  search: '/search',
  properties: '/properties',
  structure: '/structure',
}

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
  // One shared structure across every workspace - draw or paste once.
  const [smiles, setSmiles] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [status, setStatus] = useState<BackendStatus>({
    up: null,
    moleculeCount: null,
    modelLoadSeconds: null,
  })

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
    const timer = setInterval(check, 15_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!status.up || status.moleculeCount !== null) return
    moleculeStats()
      .then((body) => setStatus((s) => ({ ...s, moleculeCount: body.total })))
      .catch(() => undefined)
  }, [status.up, status.moleculeCount])

  // History without a router dependency: pushState on navigate, popstate on back.
  const navigate = useCallback((key: WorkspaceKey) => {
    setWorkspace(key)
    if (window.location.pathname !== PATHS[key]) {
      window.history.pushState({ workspace: key }, '', PATHS[key])
    }
  }, [])

  useEffect(() => {
    const onPop = () => setWorkspace(workspaceFromPath(window.location.pathname))
    window.addEventListener('popstate', onPop)
    if (window.location.pathname === '/') {
      window.history.replaceState({ workspace }, '', PATHS[workspace])
    }
    return () => window.removeEventListener('popstate', onPop)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openEditor = () => navigate('retrosynthesis')

  return (
    <div className="app">
      <Sidebar
        active={workspace}
        onSelect={navigate}
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((v) => !v)}
      />

      <div className="main">
        <TopBar status={status} onLoadSmiles={setSmiles} />

        {status.up === false && (
          <div className="offline-bar">
            Backend unreachable on <code>http://localhost:8000</code>. From the repo
            root: <code>docker compose up -d</code> then{' '}
            <code>uvicorn backend.api.main:app --port 8000</code>.
          </div>
        )}

        <main className="workspace-root">
          {workspace === 'retrosynthesis' && (
            <RetrosynthesisWorkspace
              smiles={smiles}
              onSmilesChange={setSmiles}
              backendUp={status.up}
            />
          )}

          {workspace === 'search' && (
            <div className="workspace">
              <div className="stage">
                <WorkspaceHeader
                  title="Molecular search"
                  subtitle="Find compounds in the local ChEMBL library by exact structure, similarity or substructure."
                />
                <MoleculeBar
                  smiles={smiles}
                  onSmilesChange={setSmiles}
                  onOpenEditor={openEditor}
                />
                <SearchTab smiles={smiles} />
              </div>
            </div>
          )}

          {workspace === 'properties' && (
            <div className="workspace">
              <div className="stage">
                <WorkspaceHeader
                  title="Property prediction"
                  subtitle="Predict physicochemical properties with a conformal prediction interval."
                />
                <MoleculeBar
                  smiles={smiles}
                  onSmilesChange={setSmiles}
                  onOpenEditor={openEditor}
                />
                <QsarTab smiles={smiles} />
              </div>
            </div>
          )}

          {workspace === 'structure' && (
            <div className="workspace">
              <div className="stage">
                <WorkspaceHeader
                  title="Structure"
                  subtitle="Canonical form, identifiers and depiction — computed without touching the database."
                />
                <MoleculeBar
                  smiles={smiles}
                  onSmilesChange={setSmiles}
                  onOpenEditor={openEditor}
                />
                <RepresentTab smiles={smiles} />
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
