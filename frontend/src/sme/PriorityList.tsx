/**
 * frontend/src/sme/PriorityList.tsx — drag-and-drop session priority list
 * (Phase S3). Replaces the legacy streamlit-sortables iframe: dnd-kit
 * sortable rows with status dots, fulfillment pills, code badges and SQM
 * ratios that re-render live as the TS engine re-cascades on every reorder.
 * Up/down arrow buttons mirror the drag for keyboard use (and for tests).
 */
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import type { DragEndEvent } from '@dnd-kit/core'
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Button, Tooltip, Typography } from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, CloseOutlined, HolderOutlined } from '@ant-design/icons'
import { fc } from './insights'
import type { TagStat } from './session'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

export function FulfilPill({ pct }: { pct: number }) {
  return (
    <span style={{
      ...mono, background: fc(pct), color: '#fff', borderRadius: 10,
      padding: '1px 8px', fontSize: '0.7rem', fontWeight: 700, whiteSpace: 'nowrap',
    }}>{pct.toFixed(1)}%</span>
  )
}

export function StatusDot({ pct }: { pct: number }) {
  return <span style={{
    display: 'inline-block', width: 10, height: 10, borderRadius: 5,
    background: fc(pct), flex: 'none',
  }} />
}

function SortableRow({ tag, index, count, stat, selected, onSelect, onRemove, onMove }: {
  tag: string
  index: number
  count: number
  stat?: TagStat
  selected?: boolean
  onSelect?: (tag: string) => void
  onRemove: (tag: string) => void
  onMove: (from: number, to: number) => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: tag })
  return (
    <div ref={setNodeRef} data-tag={tag}
      style={{
        transform: CSS.Transform.toString(transform), transition,
        opacity: isDragging ? 0.6 : 1,
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 8px', borderRadius: 8, marginBottom: 6,
        border: `1px solid ${selected ? '#D4AF37' : 'rgba(128,128,128,.25)'}`,
        background: isDragging ? 'rgba(212,175,55,.08)' : 'rgba(128,128,128,.05)',
        cursor: onSelect ? 'pointer' : 'default',
      }}
      onClick={onSelect ? () => onSelect(tag) : undefined}>
      <span {...attributes} {...listeners}
        style={{ cursor: 'grab', touchAction: 'none', opacity: 0.6 }}
        onClick={(e) => e.stopPropagation()} aria-label={`drag ${tag}`}>
        <HolderOutlined />
      </span>
      <span style={{ ...mono, fontSize: '0.7rem', opacity: 0.6, width: 26, flex: 'none' }}>#{index + 1}</span>
      <StatusDot pct={stat?.fulfillPct ?? 0} />
      <span style={{ minWidth: 0, flex: 1 }}>
        <Typography.Text strong style={{ ...mono, fontSize: '0.8rem' }}>{tag}</Typography.Text>
        {stat ? (
          <span style={{ fontSize: '0.72rem', opacity: 0.7, marginLeft: 6 }}>
            {stat.name.slice(0, 22)}
          </span>
        ) : (
          <span style={{ fontSize: '0.72rem', color: '#F59E0B', marginLeft: 6 }}>
            not in current site data
          </span>
        )}
        <span style={{ display: 'block', fontSize: '0.66rem', opacity: 0.6 }}>
          {stat && (
            <>
              {stat.codes.map((c) => (
                <span key={c} style={{
                  ...mono, border: '1px solid rgba(212,175,55,.5)', color: '#D4AF37',
                  borderRadius: 6, padding: '0 4px', marginRight: 4, fontSize: '0.62rem',
                }}>{c}</span>
              ))}
              {stat.location}
            </>
          )}
        </span>
      </span>
      {stat && (
        <span style={{ ...mono, fontSize: '0.68rem', opacity: 0.75, whiteSpace: 'nowrap' }}>
          {stat.canSqm.toLocaleString('en-US', { maximumFractionDigits: 1 })}
          {' / '}
          {stat.sqm.toLocaleString('en-US', { maximumFractionDigits: 1 })} SQM
        </span>
      )}
      <FulfilPill pct={stat?.fulfillPct ?? 0} />
      <span onClick={(e) => e.stopPropagation()} style={{ display: 'flex', gap: 0 }}>
        <Tooltip title="Move up">
          <Button size="small" type="text" icon={<ArrowUpOutlined />} disabled={index === 0}
            aria-label={`move ${tag} up`} onClick={() => onMove(index, index - 1)} />
        </Tooltip>
        <Tooltip title="Move down">
          <Button size="small" type="text" icon={<ArrowDownOutlined />} disabled={index === count - 1}
            aria-label={`move ${tag} down`} onClick={() => onMove(index, index + 1)} />
        </Tooltip>
        <Tooltip title="Remove from session">
          <Button size="small" type="text" danger icon={<CloseOutlined />}
            aria-label={`remove ${tag}`} onClick={() => onRemove(tag)} />
        </Tooltip>
      </span>
    </div>
  )
}

export default function PriorityList({ order, stats, onReorder, onMove, onRemove, onSelect, selected }: {
  order: string[]
  stats: Map<string, TagStat>
  onReorder: (next: string[]) => void
  onMove: (from: number, to: number) => void
  onRemove: (tag: string) => void
  onSelect?: (tag: string) => void
  selected?: string
}) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))
  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e
    if (!over || active.id === over.id) return
    const from = order.indexOf(String(active.id))
    const to = order.indexOf(String(over.id))
    if (from >= 0 && to >= 0) onReorder(arrayMove(order, from, to))
  }
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={order} strategy={verticalListSortingStrategy}>
        {order.map((tag, i) => (
          <SortableRow key={tag} tag={tag} index={i} count={order.length}
            stat={stats.get(tag)} selected={selected === tag}
            onSelect={onSelect} onRemove={onRemove} onMove={onMove} />
        ))}
      </SortableContext>
    </DndContext>
  )
}
