import {
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from 'react'
import { BrandLockup } from './BrandMark'
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
  icon: ComponentType<{ className?: string }>
  planned?: boolean
}[] = [
  { key: 'retrosynthesis', label: 'Retrosynthesis', icon: RetroIcon },
  { key: 'search', label: 'Molecular Search', icon: SearchIcon },
  { key: 'properties', label: 'Properties', icon: FlaskIcon },
  { key: 'structure', label: 'Structure', icon: BookIcon },
  { key: 'reaction', label: 'Reaction Prediction', icon: ReactionIcon, planned: true },
  { key: 'libraries', label: 'Libraries', icon: LibraryIcon, planned: true },
  { key: 'projects', label: 'Projects', icon: FolderIcon, planned: true },
]

export function Sidebar({
  active,
  onSelect,
  collapsed,
  onToggleCollapsed,
}: {
  active: WorkspaceKey
  onSelect: (key: WorkspaceKey) => void
  collapsed: boolean
  onToggleCollapsed: () => void
}) {
  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <div className="sidebar-brand">
        <BrandLockup compact={collapsed} />
        <button
          className="collapse-toggle"
          onClick={onToggleCollapsed}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <ChevronIcon />
        </button>
      </div>

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
              title={item.planned ? `${item.label} — on the roadmap, not built yet` : item.label}
            >
              <span className="nav-icon">
                <Icon />
              </span>
              {!collapsed && <span className="nav-label">{item.label}</span>}
              {!collapsed && item.planned && <span className="nav-badge">soon</span>}
            </button>
          )
        })}
      </nav>

      <div className="sidebar-foot">
        <button className="nav-item" disabled title="Settings — not built yet">
          <span className="nav-icon">
            <GearIcon />
          </span>
          {!collapsed && <span className="nav-label">Settings</span>}
        </button>
      </div>
    </aside>
  )
}

export type BackendStatus = {
  up: boolean | null
  moleculeCount: number | null
  modelLoadSeconds: number | null
}

/** Status stays available but secondary - a pill, with the implementation
 *  detail behind a popover rather than filling the sidebar. */
export function StatusPill({ status }: { status: BackendStatus }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const tone = status.up === null ? 'idle' : status.up ? 'ok' : 'bad'
  return (
    <div className="status-wrap" ref={ref}>
      <button className={`health-pill ${tone}`} onClick={() => setOpen((v) => !v)}>
        <span className="dot" />
        {status.up === null ? 'Checking…' : status.up ? 'Backend connected' : 'Backend offline'}
      </button>
      {open && (
        <div className="status-popover">
          <h4>Local instance</h4>
          <dl>
            <div>
              <dt>API</dt>
              <dd>{status.up ? 'localhost:8000' : 'unreachable'}</dd>
            </div>
            <div>
              <dt>Route models</dt>
              <dd>
                {status.modelLoadSeconds != null
                  ? `USPTO · loaded in ${status.modelLoadSeconds}s`
                  : '—'}
              </dd>
            </div>
            <div>
              <dt>Compound library</dt>
              <dd>
                {status.moleculeCount != null
                  ? `${status.moleculeCount.toLocaleString()} indexed`
                  : '—'}
              </dd>
            </div>
          </dl>
          <p>Public datasets only. Predictions are not experimentally validated.</p>
        </div>
      )}
    </div>
  )
}

export function TopBar({
  status,
  onLoadSmiles,
}: {
  status: BackendStatus
  onLoadSmiles: (smiles: string) => void
}) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

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
          placeholder="Paste a SMILES to load it into the workspace…"
          spellCheck={false}
          aria-label="Load a SMILES into the workspace"
        />
        <kbd>Ctrl K</kbd>
      </form>

      <div className="topbar-right">
        <StatusPill status={status} />
      </div>
    </header>
  )
}

export function WorkspaceHeader({
  title,
  subtitle,
  actions,
  meta,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
  meta?: ReactNode
}) {
  return (
    <header className="workspace-header">
      <div className="workspace-heading">
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
        {meta}
      </div>
      {actions && <div className="workspace-actions">{actions}</div>}
    </header>
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
