/**
 * LegacyDetailSection — collapsed-by-default section holding the legacy blocks
 * moved DOWN from the Overview body per the v2 IA (docs/overview-v2-redesign-plan.md §1).
 * Contains verbatim: OverviewGroupTile, DrillDownTable (conditional), OverviewMarketWatch,
 * DuPontTree. All components are reused without modification.
 *
 * P3: briefing data is no longer passed in from the parent. The inner LegacyBody
 * component is only mounted when the user expands the section — so
 * useOverviewBriefing (and all downstream fetches) fire ONLY on expand,
 * not on page load. This removes the last fan-out fetches from first paint.
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { Entity, FinPeriodParams } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinTab } from '../../financials/financialsTabs'
import type { DrillDownRequest } from '../../cockpit/EbitTable'
import OverviewGroupTile from '../overview/OverviewGroupTile'
import OverviewMarketWatch from '../overview/OverviewMarketWatch'
import DuPontTree from '../../cockpit/DuPontTree'
import DrillDownTable from '../../cockpit/DrillDownTable'

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  periodParams: FinPeriodParams
  cockpitPeriod: PeriodSelection
  entity?: string
  resetKey: string
  onNavigateTab: (tab: FinTab) => void
  onDrillDown: (req: DrillDownRequest) => void
  activeDrillKey?: string
  /** Active drill request (from OverviewGroupTile EBIT table). */
  drill: DrillDownRequest | null
  onCloseDrill: () => void
  /** Year + month for DuPontTree */
  year: number
  month: number
  entities: Entity[]
}

// ─── Inner body — only mounted when section is open ───────────────────────────

/**
 * Renders all legacy content. Declared as a separate component so that
 * hooks inside it (including OverviewGroupTile's own briefing fetch) are
 * only called after the user expands the section — true deferral, not
 * just bundle splitting.
 */
function LegacyBody({
  periodParams,
  cockpitPeriod,
  entity,
  resetKey,
  onNavigateTab,
  onDrillDown,
  activeDrillKey,
  drill,
  onCloseDrill,
  year,
  month,
  entities,
}: Props) {
  return (
    <div
      id="legacy-detail-body"
      className="border-t space-y-5 px-5 pb-6 pt-5"
      style={{ borderColor: '#F1F5F9' }}
    >
      {/* Group overview (Executive summary + EBIT table) — own-fetch mode */}
      <OverviewGroupTile
        periodParams={periodParams}
        cockpitPeriod={cockpitPeriod}
        entity={entity}
        resetKey={resetKey}
        onNavigateTab={onNavigateTab}
        onDrillDown={onDrillDown}
        activeDrillKey={activeDrillKey}
        hideGroupIntro
      />

      {/* Inline drill-down table (triggered from EBIT table) */}
      {drill && (
        <DrillDownTable
          entity={drill.entityCode}
          dateFrom={drill.dateFrom}
          dateTo={drill.dateTo}
          level3={drill.level3}
          statementType={drill.statementType}
          customerName={drill.customerName}
          title={drill.title}
          onClose={onCloseDrill}
        />
      )}

      {/* Top customers + suppliers */}
      <OverviewMarketWatch period={cockpitPeriod} entity={entity} />

      {/* Full DuPont tree */}
      <DuPontTree
        key={`dupont-legacy-${resetKey}`}
        title="Performance Overview"
        year={year}
        month={month}
        entities={entities}
      />
    </div>
  )
}

// ─── Shell (always rendered) ──────────────────────────────────────────────────

export default function LegacyDetailSection(props: Props) {
  const [open, setOpen] = useState(false)

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ border: '1px solid #E2E8F0', background: '#FFFFFF' }}
    >
      {/* Toggle header */}
      <button
        className="w-full flex items-center justify-between px-5 py-4 text-left transition-colors hover:bg-slate-50"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls="legacy-detail-body"
      >
        <div>
          <span className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>
            Detailed tables and charts
          </span>
          <span className="ml-3 text-xs" style={{ color: '#94A3B8' }}>
            Group overview · Top customers & suppliers · Full DuPont tree
          </span>
        </div>
        {open ? (
          <ChevronDown size={16} aria-hidden style={{ color: '#64748B', flexShrink: 0 }} />
        ) : (
          <ChevronRight size={16} aria-hidden style={{ color: '#64748B', flexShrink: 0 }} />
        )}
      </button>

      {/* LegacyBody is only mounted when open — all its hook/fetch calls are deferred */}
      {open && <LegacyBody {...props} />}
    </div>
  )
}
