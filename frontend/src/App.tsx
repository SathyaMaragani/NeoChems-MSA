import { useEffect, useState } from 'react'
import MoleculeEditor from './components/MoleculeEditor'
import RepresentTab from './components/RepresentTab'
import RetrosynthesisTab from './components/RetrosynthesisTab'
import SearchTab from './components/SearchTab'
import { retroHealth } from './api'
import './App.css'

const TABS = ['Represent', 'Retrosynthesis', 'Search'] as const
type Tab = (typeof TABS)[number]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('Represent')
  // Single source of truth for the structure, shared by the editor and every tab.
  const [smiles, setSmiles] = useState('')
  const [backendUp, setBackendUp] = useState<boolean | null>(null)

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

  return (
    <div className="app">
      <header className="app-header">
        <h1>RamChems</h1>
        <span className="subtitle">retrosynthesis + molecular search</span>
        <span className={`health health-${backendUp === null ? 'unknown' : backendUp ? 'up' : 'down'}`}>
          {backendUp === null
            ? 'checking backend...'
            : backendUp
              ? 'backend connected'
              : 'backend not running'}
        </span>
      </header>

      {backendUp === false && (
        <div className="banner">
          The backend is not reachable on <code>http://localhost:8000</code>. Start it
          from the repo root:{' '}
          <code>uvicorn backend.api.main:app --port 8000</code> (and{' '}
          <code>docker compose up -d</code> for the search database).
        </div>
      )}

      <main className="layout">
        <section className="panel editor-panel">
          <h2>Structure</h2>
          <MoleculeEditor smiles={smiles} onSmilesChange={setSmiles} />
        </section>

        <section className="panel actions-panel">
          <nav className="tabs">
            {TABS.map((tab) => (
              <button
                key={tab}
                className={tab === activeTab ? 'tab active' : 'tab'}
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </button>
            ))}
          </nav>
          <div className="tab-body">
            {activeTab === 'Represent' && <RepresentTab smiles={smiles} />}
            {activeTab === 'Retrosynthesis' && <RetrosynthesisTab smiles={smiles} />}
            {activeTab === 'Search' && <SearchTab smiles={smiles} />}
          </div>
        </section>
      </main>
    </div>
  )
}
