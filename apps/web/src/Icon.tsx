import type { CSSProperties } from 'react'

export type IconName = 'upload' | 'file' | 'check' | 'arrow' | 'chevron' | 'download' | 'close' | 'plus' | 'search' | 'book' | 'layers' | 'warning' | 'refresh' | 'external' | 'grid'
const paths: Record<IconName, string> = {
  upload: 'M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4',
  file: 'M14 2H5a1 1 0 0 0-1 1v18a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8l-6-6Zm0 0v6h6M8 13h8M8 17h5',
  check: 'm5 12 4 4L19 6',
  arrow: 'M4 12h16m-6-6 6 6-6 6',
  chevron: 'm9 5 7 7-7 7',
  download: 'M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4',
  close: 'm6 6 12 12M6 18 18 6',
  plus: 'M12 5v14M5 12h14',
  search: 'm21 21-5-5M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z',
  book: 'M12 5v16M3 3h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5v16h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3V3Z',
  layers: 'm12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5',
  warning: 'm12 3 10 18H2L12 3Zm0 6v5m0 3h.01',
  refresh: 'M20 7a9 9 0 1 0 1 8M20 2v5h-5',
  external: 'M14 3h7v7m0-7L10 14M10 3H3v18h18v-7',
  grid: 'M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z',
}

export default function Icon({ name, size = 18, style }: { name: IconName; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}><path d={paths[name]} /></svg>
}
