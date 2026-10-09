import { useQuery } from '@tanstack/react-query'
import { PictureOutlined } from '@ant-design/icons'
import { api, apiBase } from '../api/client'

/**
 * Phase 23d — material pictures anywhere in the app (rulings Q23-6..8).
 *
 * A list asks once for the thumbnails of every code / SAP on it
 * (`GET /catalogue/thumbs`, ≤ 300 a call) and gets SIGNED links back: an
 * `<img>` cannot send the sign-in token, so the link itself carries an
 * hour-long signature. `loading="lazy"` means a long table only fetches the
 * pictures that scroll into view.
 */
export interface ThumbMap { codes: Record<string, string>; saps: Record<string, string> }

export const imgSrc = (u?: string | null) => (u ? `${apiBase()}${u}` : undefined)

function chunks<T>(xs: T[], n: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n))
  return out
}

export function useThumbs({ saps = [], codes = [], size = 'thumb' }:
  { saps?: string[]; codes?: string[]; size?: 'thumb' | 'display' }) {
  const s = [...new Set(saps.filter(Boolean))].sort()
  const c = [...new Set(codes.filter(Boolean).map((x) => x.toUpperCase()))].sort()
  return useQuery<ThumbMap>({
    queryKey: ['/catalogue/thumbs', size, s.join(','), c.join(',')],
    enabled: s.length + c.length > 0,
    staleTime: 30 * 60_000,
    queryFn: async () => {
      const out: ThumbMap = { codes: {}, saps: {} }
      const sc = chunks(s, 250)
      const cc = chunks(c, 250)
      for (let i = 0; i < Math.max(sc.length, cc.length); i += 1) {
        const r = (await api.get<ThumbMap>('/catalogue/thumbs', {
          params: { saps: (sc[i] ?? []).join(',') || undefined, codes: (cc[i] ?? []).join(',') || undefined, size },
        })).data
        Object.assign(out.codes, r.codes)
        Object.assign(out.saps, r.saps)
      }
      return out
    },
  })
}

export function Thumb({ src, size = 32, alt = '' }: { src?: string | null; size?: number; alt?: string }) {
  const box = {
    width: size, height: size, borderRadius: 4, flex: '0 0 auto',
    border: '1px solid var(--ant-color-border-secondary)', objectFit: 'cover' as const,
  }
  if (!src) {
    return (
      <span aria-hidden style={{ ...box, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', opacity: 0.35 }}>
        <PictureOutlined style={{ fontSize: Math.round(size * 0.5) }} />
      </span>
    )
  }
  return <img src={imgSrc(src)} alt={alt} loading="lazy" decoding="async" width={size} height={size} style={box} />
}

// ── one cell, one SAP — batched ──────────────────────────────────────────────
// A table cell asks for its own SAP's picture; the asks made within the same
// few milliseconds go out as ONE /catalogue/thumbs call, and react-query keeps
// each answer, so a 50-row page costs one request, not fifty.
let pending: { sap: string; resolve: (v: string | null) => void }[] = []
let timer: ReturnType<typeof setTimeout> | null = null

function flush() {
  const batch = pending
  pending = []
  timer = null
  const saps = [...new Set(batch.map((b) => b.sap))]
  api.get<ThumbMap>('/catalogue/thumbs', { params: { saps: saps.join(',') } })
    .then((r) => batch.forEach((b) => b.resolve(r.data.saps[b.sap] ?? null)))
    .catch(() => batch.forEach((b) => b.resolve(null)))
}

function thumbForSap(sap: string): Promise<string | null> {
  return new Promise((resolve) => {
    pending.push({ sap, resolve })
    if (pending.length >= 250) {
      if (timer) clearTimeout(timer)
      flush()
    } else if (!timer) {
      timer = setTimeout(flush, 15)
    }
  })
}

export function SapThumb({ sap, size = 28 }: { sap: string; size?: number }) {
  const { data } = useQuery<string | null>({
    queryKey: ['/catalogue/thumb', sap],
    enabled: !!sap,
    staleTime: 30 * 60_000,
    queryFn: () => thumbForSap(sap),
  })
  return <Thumb src={data} size={size} />
}
