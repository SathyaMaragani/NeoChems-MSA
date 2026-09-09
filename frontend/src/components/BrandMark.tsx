/** RC mark — an aromatic ring closing into a C, with the R stem across it.
 *  Vector so it holds up at 18px in a collapsed sidebar and at 64px on a
 *  loading screen; no plate or square behind it. */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      aria-hidden
      className="brand-mark-svg"
    >
      <defs>
        <linearGradient id="rc-a" x1="4" y1="44" x2="44" y2="4" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="45%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#a855f7" />
        </linearGradient>
        <linearGradient id="rc-b" x1="10" y1="10" x2="40" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#67e8f9" />
          <stop offset="100%" stopColor="#818cf8" />
        </linearGradient>
      </defs>

      {/* aromatic ring, opening to the right like a C */}
      <path
        d="M31.5 9.6 A17 17 0 1 0 31.5 38.4"
        stroke="url(#rc-a)"
        strokeWidth="4.2"
        strokeLinecap="round"
      />
      {/* inner ring hint */}
      <path
        d="M28.8 15.4 A11 11 0 1 0 28.8 32.6"
        stroke="url(#rc-b)"
        strokeWidth="1.6"
        strokeLinecap="round"
        opacity="0.55"
      />
      {/* R stem + leg */}
      <path
        d="M17 14.5v19M17 14.5h5.6a5.2 5.2 0 0 1 0 10.4H17M22.4 24.9 30 33.5"
        stroke="url(#rc-b)"
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* bonded node */}
      <circle cx="37.6" cy="24" r="3.6" fill="url(#rc-a)" />
      <path d="M33 24h1.2" stroke="url(#rc-a)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

export function BrandLockup({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand${compact ? ' compact' : ''}`}>
      <BrandMark size={compact ? 26 : 30} />
      {!compact && (
        <div className="brand-text">
          <span className="brand-name">RamChems</span>
          <span className="brand-sub">Computational chemistry workspace</span>
        </div>
      )}
    </div>
  )
}
