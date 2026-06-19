import { useState } from 'react'
import { ChevronDown, SlidersHorizontal } from 'lucide-react'
import ModulePeriodFilterBar from './ModulePeriodFilterBar'
import type { Entity } from '../../lib/api'
import type { LatestPeriodInfo, PeriodGrain, PeriodSelection } from '../../lib/periodSelection'

type Props = {
  entities: Entity[]
  entity: string
  onEntityChange: (code: string) => void
  grain: PeriodGrain
  onGrainChange: (g: PeriodGrain) => void
  period: PeriodSelection
  onPeriodChange: (p: PeriodSelection) => void
  latest: LatestPeriodInfo | null
  loading?: boolean
  onRefresh?: () => void
  /** When true, show Annual pill in embedded ModulePeriodFilterBar (Overview / P&L). */
  showYearGrain?: boolean
}

export default function CollapsibleModuleFiltersCard({
  entities,
  entity,
  onEntityChange,
  grain,
  onGrainChange,
  period,
  onPeriodChange,
  latest,
  loading,
  onRefresh,
  showYearGrain,
}: Props) {
  const [open, setOpen] = useState(false)

  return (
    <div
      className="rounded-xl mb-6 overflow-hidden"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
    >
      <div
        className="px-5 py-3 border-b flex items-center justify-between gap-3"
        style={{ borderColor: '#F1F5F9', background: '#FAFBFC' }}
      >
        <h2
          className="text-xs font-semibold uppercase tracking-wide"
          style={{ color: '#64748B' }}
        >
          Filters
        </h2>
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
          style={{
            background: open ? 'rgba(30,58,95,0.1)' : '#F4F6F9',
            color: '#1E3A5F',
            border: `1px solid ${open ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
          }}
          aria-expanded={open}
          aria-label={open ? 'Hide filters' : 'Show filters'}
        >
          <SlidersHorizontal size={14} />
          <span>Filters</span>
          <ChevronDown
            size={14}
            style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}
          />
        </button>
      </div>

      {open && (
        <div className="px-5 py-4">
          <p
            className="text-[10px] font-semibold uppercase tracking-wide mb-3"
            style={{ color: '#94A3B8' }}
          >
            Period & entity
          </p>
          <ModulePeriodFilterBar
            embedded
            entities={entities}
            entity={entity}
            onEntityChange={onEntityChange}
            grain={grain}
            onGrainChange={onGrainChange}
            period={period}
            onPeriodChange={onPeriodChange}
            latest={latest}
            loading={loading}
            onRefresh={onRefresh}
            showYearGrain={showYearGrain}
          />
        </div>
      )}
    </div>
  )
}
