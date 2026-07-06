/**
 * frontend/src/sme/CoverageHBar.tsx — horizontal coverage bars (Phase S2).
 * Native React port of the legacy render_design_hbar() (material_estimator_
 * portal.py:3215): 460-wide viewBox, 24px bars, 7px gap, 140px label gutter,
 * tier-colored fills (fc), monospace % readout. Hand-drawn SVG — no library.
 */
import { fc } from './insights'

const W = 460, BH = 24, GAP = 7, PADL = 140, PADR = 60
const IW = W - PADL - PADR

export default function CoverageHBar({ data, title }: {
  data: { label: string; val: number }[]
  title?: string
}) {
  if (!data.length) {
    return <div style={{ color: '#94A3B8', fontSize: 12 }}>{title ? `${title} — ` : ''}No data.</div>
  }
  const maxV = Math.max(...data.map((d) => d.val || 0), 1)
  const totalH = data.length * (BH + GAP) + 16
  return (
    <div>
      {title && (
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: '0.62rem', fontWeight: 700,
          letterSpacing: '.13em', textTransform: 'uppercase', color: '#94A3B8', marginBottom: 8,
        }}>{title}</div>
      )}
      <svg viewBox={`0 0 ${W} ${totalH}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%' }}>
        {data.map((d, i) => {
          const v = d.val || 0
          const y = i * (BH + GAP) + 6
          const bW = Math.max(2, (v / maxV) * IW)
          const col = fc(v)
          return (
            <g key={`${d.label}-${i}`}>
              <text x={PADL - 7} y={y + BH / 2 + 4} textAnchor="end" fill="#94A3B8"
                fontSize={11} fontFamily="Inter, sans-serif">{d.label.slice(0, 20)}</text>
              <rect x={PADL} y={y} width={IW} height={BH} rx={4} fill="rgba(128,128,128,.12)" />
              <rect x={PADL} y={y} width={bW} height={BH} rx={4} fill={col} opacity={0.85} />
              <text x={PADL + bW + 5} y={y + BH / 2 + 4} fill="#94A3B8" fontSize={11}
                fontFamily="JetBrains Mono, monospace">{v.toFixed(1)}%</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
