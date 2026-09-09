import { useEffect, useRef, useState, type ComponentType, type ReactNode } from 'react'
import logo from '../assets/logo.png'
import {
  BookIcon,
  ChevronIcon,
  FlaskIcon,
  FolderIcon,
  GearIcon,
  LibraryIcon,
  ReactionIcon,
  RetroIcon,
  SearchIcon,
} from './icons'

export type WorkspaceKey = 'retrosynthesis' | 'search' | 'properties' | 'structure'

/** Destinations that exist. `planned` items are on the roadmap but not built -
 *  shown so the shape of the product is visible, disabled so nothing pretends
 *  to work. */
const NAV: {
  key: WorkspaceKey | string
  label: string
  hint: string
  icon: ComponentType<{ className?: string }>
  planned?: boolean
}[] = [
  { key: 'retrosynthesis', label: 'Retrosynthesis', hint: 'Plan synthetic routes', icon: RetroIcon },
  { key: 'search', label: 'Molecular Search', hint: 'Find similar compounds', icon: SearchIcon },
  { key: 'properties', label: 'Properties', hint: 'Predict solubility', icon: FlaskIcon },
  { key: 'structure', label: 'Structure', hint: 'Canonical form & identifiers', icon: BookIcon },
  { key: 'reaction', label: 'Reaction Prediction', hint: 'Planned', icon: ReactionIcon, planned: true },
  { key: 'libraries', label: 'Libraries', hint: 'Planned', icon: LibraryIcon, planned: true },
  { key: 'projects', label: 'Projects', hint: 'Planned', icon: FolderIcon, planned: true },
]

export function Sidebar({
  active,
  onSelect,
  backendUp,
  moleculeCount,
}: {
  active: WorkspaceKey
  onSelect: (key: WorkspaceKey) => void
  backendUp: boolean | null
  moleculeCount: number | null
}) {
  return (
    <aside className="sidebar">
      <nav className="nav">
        {NAV.map((item) => {
          const Icon = item.icon
          const isActive = !item.planned && item.key === active
          return (
            <button
              key={item.key}
              className={`nav-item${isActive ? ' active' : ''}${item.planned ? ' planned' : ''}`}
              onClick={() => !item.planned && onSelect(item.key as WorkspaceKey)}
              disabled={item.planned}
              title={item.planned ? 'On the roadmap, not built yet' : undefined}
            >
              <span className="nav-icon">
                <Icon />
              </span>
              <span className="nav-text">
                <span className="nav-label">{item.label}</span>
                <span className="nav-hint">{item.hint}</span>
              </span>
              {item.planned && <span className="nav-badge">soon</span>}
            </button>
          )
        })}
      </nav>

      <div className="sidebar-foot">
        {/* Real status, not a promo slot. */}
        <div className="status-card">
          <div className="status-title">Local instance</div>
          <ul className="status-list">
            <li>
              <span className={`dot ${backendUp ? 'ok' : backendUp === null ? 'idle' : 'bad'}`} />
              API {backendUp === null ? 'checking' : backendUp ? 'connected' : 'offline'}
            </li>
            <li>
              <span className={`dot ${moleculeCount ? 'ok' : 'idle'}`} />
              {moleculeCount ? moleculeCount.toLocaleString() : '—'} compounds indexed
            </li>
            <li>
              <span className="dot ok" />
              USPTO route models loaded
            </li>
          </ul>
          <p className="status-note">
            Public datasets only. Predictions are not experimentally validated.
          </p>
        </div>

        <a
          className="sidebar-link"
          href="https://github.com/aspuru-guzik-group/aizynthfinder"
          target="_blank"
          rel="noreferrer"
        >
          <BookIcon /> Documentation
        </a>
        <button className="sidebar-link" disabled title="On the roadmap, not built yet">
          <GearIcon /> Settings
        </button>
      </div>
    </aside>
  )
}

export function TopBar({
  backendUp,
  onLoadSmiles,
}: {
  backendUp: boolean | null
  onLoadSmiles: (smiles: string) => void
}) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // Ctrl/Cmd-K focuses the jump box, the one shortcut worth having.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <header className="topbar">
      <div className="brand">
        <img className="brand-mark" src={logo} alt="" />
        <div className="brand-text">
          <span className="brand-name">RamChems</span>
          <span className="brand-sub">Retrosynthesis · Molecular Search · Properties</span>
        </div>
      </div>

      <form
        className="jump"
        onSubmit={(event) => {
          event.preventDefault()
          const value = query.trim()
          if (value) {
            onLoadSmiles(value)
            setQuery('')
            inputRef.current?.blur()
          }
        }}
      >
        <SearchIcon className="jump-icon" />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Paste a SMILES to load it into the editor…"
          spellCheck={false}
          aria-label="Load a SMILES into the editor"
        />
        <kbd>Ctrl K</kbd>
      </form>

      <div className="topbar-right">
        <span className={`health-pill ${backendUp === null ? 'idle' : backendUp ? 'ok' : 'bad'}`}>
          <span className="dot" />
          {backendUp === null ? 'Checking…' : backendUp ? 'Backend connected' : 'Backend offline'}
        </span>
      </div>
    </header>
  )
}

export function PanelTabs<T extends string>({
  tabs,
  active,
  onSelect,
}: {
  tabs: { key: T; label: string }[]
  active: T
  onSelect: (key: T) => void
}) {
  return (
    <nav className="panel-tabs">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          className={tab.key === active ? 'panel-tab active' : 'panel-tab'}
          onClick={() => onSelect(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  )
}

export function Collapsible({
  title,
  children,
  defaultOpen = false,
}: {
  title: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={`collapsible${open ? ' open' : ''}`}>
      <button className="collapsible-head" onClick={() => setOpen((v) => !v)}>
        <GearIcon />
        <span>{title}</span>
        <ChevronIcon className="collapsible-chevron" />
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </section>
  )
}
