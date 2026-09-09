import { useEffect, useState } from 'react'
import MoleculeEditor from './components/MoleculeEditor'
import QsarTab from './components/QsarTab'
import RepresentTab from './components/RepresentTab'
import RetrosynthesisTab from './components/RetrosynthesisTab'
import SearchTab from './components/SearchTab'
import { PanelTabs, Sidebar, TopBar, type WorkspaceKey } from './components/Shell'
import { moleculeStats, retroHealth } from './api'
import './App.css'

const WORKSPACE_TABS: { key: WorkspaceKey; label: string }[] = [
  { key: 'retrosynthesis', label: 'Retrosynthesis' },
  { key: 'search', label: 'Molecular Search' },
  { key: 'properties', label: 'Properties' },
  { key: 'structure', label: 'Structure' },
]

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceKey>('retrosynthesis')
  // Single source of truth for the structure, shared by the editor and every panel.
  const [smiles, setSmiles] = useState('')
  const [backendUp, setBackendUp] = useState<boolean | null>(null)
  const [moleculeCount, setMoleculeCount] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    const check = () =>
      retroHealth()
        .then(() => !cancelled && setBackendUp(true))
        .catch(() => !cancelled && setBackendUp(false))
    check()
    const timer = setInterval(check, 15_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  // A real total from the table. substructure_search would have capped at top_n
  // and reported the cap as the library size.
  useEffect(() => {
    if (!backendUp || moleculeCount !== null) return
    moleculeStats()
      .then((body) => setMoleculeCount(body.total))
      .catch(() => undefined)
  }, [backendUp, moleculeCount])

  return (
    <div className="app">
      <Sidebar
        active={workspace}
        onSelect={setWorkspace}
        backendUp={backendUp}
        moleculeCount={moleculeCount}
      />

      <div className="main">
        <TopBar backendUp={backendUp} onLoadSmiles={setSmiles} />

        {backendUp === false && (
          <div className="offline-bar">
            Backend unreachable on <code>http://localhost:8000</code>. From the repo
            root: <code>docker compose up -d</code> then{' '}
            <code>uvicorn backend.api.main:app --port 8000</code>.
          </div>
        )}

        <div className="columns">
          <section className="panel input-panel">
            <div className="panel-head">
              <h2>Structure</h2>
              <span className="panel-head-note">shared across every workspace</span>
            </div>
            <div className="panel-scroll">
              <MoleculeEditor smiles={smiles} onSmilesChange={setSmiles} />
            </div>
          </section>

          <section className="panel output-panel">
            <PanelTabs tabs={WORKSPACE_TABS} active={workspace} onSelect={setWorkspace} />
            <div className="panel-scroll">
              {workspace === 'retrosynthesis' && <RetrosynthesisTab smiles={smiles} />}
              {workspace === 'search' && <SearchTab smiles={smiles} />}
              {workspace === 'properties' && <QsarTab smiles={smiles} />}
              {workspace === 'structure' && <RepresentTab smiles={smiles} />}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
