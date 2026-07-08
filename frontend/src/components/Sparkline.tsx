/**
 * frontend/src/components/Sparkline.tsx — tiny dependency-free inline-SVG
 * sparkline (no Recharts on the entry pages → keeps the chunk small).
 */
export default function Sparkline({
  data, width = 132, height = 30, stroke = 'var(--gi-gold)',
}: { data: number[]; width?: number; height?: number; stroke?: string }) {
  if (!data.length) return null
  const max = Math.max(1, ...data)
  const n = data.length
  const pts = data.map((v, i) => {
    const x = n === 1 ? width / 2 : (i / (n - 1)) * width
    const y = height - (v / max) * (height - 2) - 1
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const last = data[n - 1]
  const lx = n === 1 ? width / 2 : width
  const ly = height - (last / max) * (height - 2) - 1
  return (
    <svg width={width} height={height} role="img" aria-label="30-day consumption trend"
      style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r={2} fill={stroke} />
    </svg>
  )
}
