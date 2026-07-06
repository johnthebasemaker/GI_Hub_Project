/**
 * frontend/src/sme/CoverageGauge.tsx — half-gauge SVG (Phase S2).
 * Native React port of the legacy render_design_gauge() (material_estimator_
 * portal.py:3169): same geometry (300×168, R115), same tier band arcs
 * (red 0–50 · yellow 50–70 · orange 70–85 · green 85–100), same JetBrains
 * Mono readout. Zero chart-library dependency — it's hand-drawn SVG, so we
 * keep the exact brand look.
 */
import { fc } from './insights'

const W = 300, H = 168, CX = 150, CY = 158, R = 115
const SA = -Math.PI

function arc(r: number, s: number, e: number): string {
  const x1 = CX + r * Math.cos(s), y1 = CY + r * Math.sin(s)
  const x2 = CX + r * Math.cos(e), y2 = CY + r * Math.sin(e)
  const large = e - s > Math.PI ? 1 : 0
  return `M ${x1.toFixed(1)} ${y1.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`
}

const nf1 = (v: number) => v.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

export default function CoverageGauge({ pct, canSqm, totalSqm }: {
  pct: number
  canSqm: number
  totalSqm: number
}) {
  const p = Math.max(0, Math.min(100, pct || 0))
  const vA = SA + (p / 100) * Math.PI
  const col = fc(p)
  return (
    <div style={{ textAlign: 'center' }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ maxWidth: 340, width: '100%' }}>
        <path d={arc(R, SA, SA + 0.5 * Math.PI)} fill="none" stroke="rgba(239,68,68,.18)" strokeWidth={22} />
        <path d={arc(R, SA + 0.5 * Math.PI, SA + 0.7 * Math.PI)} fill="none" stroke="rgba(234,179,8,.18)" strokeWidth={22} />
        <path d={arc(R, SA + 0.7 * Math.PI, SA + 0.85 * Math.PI)} fill="none" stroke="rgba(249,115,22,.18)" strokeWidth={22} />
        <path d={arc(R, SA + 0.85 * Math.PI, 0)} fill="none" stroke="rgba(16,185,129,.18)" strokeWidth={22} />
        <path d={arc(R, SA, 0)} fill="none" stroke="rgba(128,128,128,.18)" strokeWidth={20} />
        {p > 0 && (
          <path d={arc(R, SA, vA)} fill="none" stroke={col} strokeWidth={20} strokeLinecap="round" />
        )}
        <text x={CX - R + 2} y={CY + 20} fill="#94A3B8" fontSize={10} fontFamily="JetBrains Mono, monospace">0%</text>
        <text x={CX + R - 22} y={CY + 20} fill="#94A3B8" fontSize={10} fontFamily="JetBrains Mono, monospace">100%</text>
        <text x={CX} y={CY - 20} textAnchor="middle" fill={col} fontSize={32} fontWeight={800}
          fontFamily="JetBrains Mono, monospace">{p.toFixed(1)}%</text>
        <text x={CX} y={CY - 2} textAnchor="middle" fill="#94A3B8" fontSize={11}
          fontFamily="Inter, sans-serif">Overall Coverage</text>
        <text x={CX} y={CY + 14} textAnchor="middle" fill="#94A3B8" fontSize={10}
          fontFamily="JetBrains Mono, monospace">{nf1(canSqm)} / {nf1(totalSqm)} SQM</text>
      </svg>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 16, fontSize: 12, color: '#94A3B8', fontFamily: 'JetBrains Mono, monospace' }}>
        <span><span style={{ color: '#10B981' }}>■</span> Available: {nf1(canSqm)} SQM</span>
        <span><span style={{ color: '#EF4444' }}>■</span> Shortfall: {nf1(Math.max(0, totalSqm - canSqm))} SQM</span>
      </div>
    </div>
  )
}
