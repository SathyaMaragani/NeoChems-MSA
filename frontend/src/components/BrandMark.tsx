import logoMark from '../assets/logo-mark.png'

/** The RC lockup. The source art is 1536x1024 and 1.5 MB; these are cropped to
 *  the artwork's own alpha bounds and resized for the web (16 KB), so the
 *  transparent background sits directly on the dark sidebar with no plate. */
export function BrandMark({ height = 30 }: { height?: number }) {
  return (
    <img
      className="brand-mark-img"
      src={logoMark}
      alt=""
      style={{ height }}
      // 2x source, so it stays crisp on hidpi at the sizes used here.
      width={(135 / 88) * height}
      height={height}
    />
  )
}

export function BrandLockup({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand${compact ? ' compact' : ''}`}>
      <BrandMark height={compact ? 26 : 32} />
      {!compact && (
        <div className="brand-text">
          <span className="brand-name">RamChems</span>
          <span className="brand-sub">Computational chemistry workspace</span>
        </div>
      )}
    </div>
  )
}
