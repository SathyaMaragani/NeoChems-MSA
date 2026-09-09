/** Inline SVG icons — no icon dependency for a handful of glyphs. */
type Props = { className?: string }

const base = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export const RetroIcon = (p: Props) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 4a8 8 0 0 1 0 16" strokeDasharray="2 3" />
    <circle cx="12" cy="12" r="2.5" />
  </svg>
)

export const SearchIcon = (p: Props) => (
  <svg {...base} {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
)

export const FlaskIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="M9 3h6M10 3v6l-5.5 9A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-3L14 9V3" />
    <path d="M7.5 15h9" />
  </svg>
)

export const ReactionIcon = (p: Props) => (
  <svg {...base} {...p}>
    <circle cx="5" cy="7" r="2.2" />
    <circle cx="5" cy="17" r="2.2" />
    <path d="M8 8.5 13 12l-5 3.5M13 12h7" />
  </svg>
)

export const LibraryIcon = (p: Props) => (
  <svg {...base} {...p}>
    <rect x="3" y="4" width="6" height="16" rx="1.5" />
    <rect x="11" y="4" width="4" height="16" rx="1.5" />
    <path d="m17.5 5 3.2 14" />
  </svg>
)

export const FolderIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="M3 7a2 2 0 0 1 2-2h3.6l2 2.4H19a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
  </svg>
)

export const GearIcon = (p: Props) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.5v2.2M12 19.3v2.2M21.5 12h-2.2M4.7 12H2.5M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6M18.7 18.7l-1.6-1.6M6.9 6.9 5.3 5.3" />
  </svg>
)

export const BookIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H19v14H5.5A1.5 1.5 0 0 0 4 19.5Z" />
    <path d="M4 19.5A1.5 1.5 0 0 1 5.5 18H19v2H5.5A1.5 1.5 0 0 1 4 19.5Z" />
  </svg>
)

export const ChevronIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="m6 9 6 6 6-6" />
  </svg>
)

export const CopyIcon = (p: Props) => (
  <svg {...base} {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h8" />
  </svg>
)

export const CheckIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="m5 13 4.5 4.5L19 7" />
  </svg>
)

export const SparkIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9Z" />
    <path d="M18.5 15.5 19.4 18l2.5.9-2.5.9-.9 2.5-.9-2.5-2.5-.9 2.5-.9Z" />
  </svg>
)

export const ResetIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="M4 10a8 8 0 1 1 .9 5" />
    <path d="M3.4 4.6 4 10l5.4-.6" />
  </svg>
)

export const ArrowRightIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="M4 12h15M13 6l6 6-6 6" />
  </svg>
)

export const InfoIcon = (p: Props) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 7.8v.4" />
  </svg>
)

export const AlertIcon = (p: Props) => (
  <svg {...base} {...p}>
    <path d="M12 4.5 21 19.5H3Z" />
    <path d="M12 10v3.5M12 16.6v.3" />
  </svg>
)
