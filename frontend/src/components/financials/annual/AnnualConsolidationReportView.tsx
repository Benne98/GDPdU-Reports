import { useMemo, useState } from 'react'

import type { ConsolidationResponse, ErSnapshotColLabels, FinancialStatementRow } from '../../../lib/api'

import type { FinancialsDrillOpen } from '../FinancialStatementTable'

import { FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'

import StatementSectionHeading from '../statement-two-view/StatementSectionHeading'

import StatementNarrativeList from '../statement-two-view/StatementNarrativeList'

import type { PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'
import PlDetailOverlay from '../pl-two-view/PlDetailOverlay'

import { ChartLoadReporter } from '../../../hooks/useChartLoadReporter'

import AnnualConsolidationGridMiniTable from './AnnualConsolidationGridMiniTable'

import {
  buildAnnualConsolidationNarrativeResponse,
  mergeAnnualConsolidationNarrative,
} from './annualConsolidationNarrative'

import {
  buildAnnualConsolidationCommentMarkerMap,
  isGenericSnapshotBulletText,
  prepareAnnualConsolidationReportBullets,
} from './annualReportMarkers'

import { KEY_DRIVERS_HEADING, buildAnnualSnapshotReportTableHeading } from './annualReportSectionHeadings'

import { useAnnualStatementNarrative } from './useAnnualStatementNarrative'

import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import { labelActual } from '../../../lib/periodColumnLabels'
import { usePlRowExpansion } from '../pl-two-view/usePlRowExpansion'

type Props = {
  consol: ConsolidationResponse
  year: number
  month: number
  ytdLabel: string
  periodSelection: PeriodSelection
  statement?: FinStatementKind
  onDrill: (d: FinancialsDrillOpen) => void
}

function snapshotColLabels(
  consol: ConsolidationResponse,
  year: number,
  month: number,
): ErSnapshotColLabels {
  if (consol.col_labels) {
    return {
      dec_py2: consol.col_labels.dec_py2,
      fy_py: consol.col_labels.fy_py ?? '',
      fy: consol.col_labels.fy ?? '',
      cm_py: consol.col_labels.cm_py ?? '',
      fy_f: consol.col_labels.fy_f,
      cm: consol.col_labels.cm ?? '',
    }
  }
  const abbr = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month - 1] ?? 'Jan'
  return {
    fy_py: labelActual(`Dec${String(year - 2).slice(-2)}`),
    fy: labelActual(`Dec${String(year - 1).slice(-2)}`),
    cm_py: labelActual(`${abbr}${String(year - 1).slice(-2)}`),
    cm: labelActual(`${abbr}${String(year).slice(-2)}`),
  }
}

const FLOW_TABLE_HEADING = 'Consolidated Income statement'

export default function AnnualConsolidationReportView({
  consol,
  year,
  month,
  ytdLabel,
  periodSelection,
  statement = 'pl',
  onDrill,
}: Props) {
  const [detailBullet, setDetailBullet] = useState<PlNarrativeBullet | null>(null)
  const clientNarrative = useMemo(
    () => buildAnnualConsolidationNarrativeResponse(consol, ytdLabel),
    [consol, ytdLabel],
  )

  const { narrative: fetchedNarrative, narrativeBusy, apiNarrative } = useAnnualStatementNarrative(
    statement,
    year,
    month,
    clientNarrative,
    undefined,
    periodSelection,
  )

  const narrative = useMemo(
    () => mergeAnnualConsolidationNarrative(fetchedNarrative, clientNarrative, consol, ytdLabel, statement),
    [fetchedNarrative, clientNarrative, consol, ytdLabel, statement],
  )

  const expansionStatement =
    statement === 'wc' ? 'wc' : statement === 'bs' ? 'bs' : statement === 'cf' ? 'cf' : 'pl'
  // PL group report: auto-expand depth 0+1 so L4 detail children under L3 mapping rows are
  // visible by default — matches BS visual parity. All other statements use their own defaults.
  const plGroupMaxDepth = statement === 'pl' ? 2 : undefined
  const { checkOpen, toggle } = usePlRowExpansion(
    consol.rows as unknown as FinancialStatementRow[],
    expansionStatement,
    plGroupMaxDepth,
  )

  const bullets = useMemo((): PlNarrativeBullet[] => {
    const raw = (narrative.bullets ?? []).map(b => ({
      index: b.index,
      line_code: b.line_code,
      label: b.label,
      priority: 0,
      text: b.text,
      tone: (b.tone as PlNarrativeBullet['tone']) ?? 'neutral',
    }))
    const prepared = prepareAnnualConsolidationReportBullets(raw, consol.rows, checkOpen)
    if (prepared.length) return prepared

    const clientRaw = (clientNarrative.bullets ?? []).map(b => ({
      index: b.index,
      line_code: b.line_code,
      label: b.label,
      priority: 0,
      text: b.text,
      tone: (b.tone as PlNarrativeBullet['tone']) ?? 'neutral',
    }))
    const clientPrepared = prepareAnnualConsolidationReportBullets(clientRaw, consol.rows, checkOpen)
    if (clientPrepared.length) return clientPrepared

    // Last-resort: show raw API narrative bullets without consolidation-row anchoring.
    // Activates when the snapshot was cold (404) AND all consolidation row values are
    // below materiality threshold — e.g. during a data-repair window. No table markers
    // are placed in this mode but the narrative panel still shows meaningful text.
    const rawApi = (apiNarrative?.bullets ?? []).filter(b => !isGenericSnapshotBulletText(b.text))
    return rawApi.map((b, i) => ({
      index: i + 1,
      line_code: b.line_code,
      label: b.label ?? '',
      priority: 0,
      text: b.text,
      tone: (b.tone as PlNarrativeBullet['tone']) ?? 'neutral',
    }))
  }, [narrative, clientNarrative, consol.rows, checkOpen, apiNarrative])

  const commentMarkersByLineCode = useMemo(
    () => buildAnnualConsolidationCommentMarkerMap(bullets, consol.rows, checkOpen),
    [bullets, consol.rows, checkOpen],
  )

  const tableHeading = useMemo(() => {
    if (statement === 'bs' || statement === 'wc') {
      return buildAnnualSnapshotReportTableHeading(statement, snapshotColLabels(consol, year, month))
    }
    if (statement === 'cf') {
      return `Consolidated Cash flow statement — ${ytdLabel}`
    }
    return FLOW_TABLE_HEADING
  }, [statement, consol, year, month, ytdLabel])

  const chartId =
    statement === 'bs'
      ? 'fin-report-annual-bs-consolidation'
      : statement === 'wc'
        ? 'fin-report-annual-wc-consolidation'
        : statement === 'cf'
          ? 'fin-report-annual-cf-consolidation'
          : 'fin-report-annual-pl-consolidation'

  return (
    <>
    <div className="px-4 pt-6 pb-6">
      <ChartLoadReporter chartId={chartId} loading={narrativeBusy} />
      <div className={FIN_ENTITY_CONSOL_REPORT_SPLIT_GRID}>
        <div className="min-w-0 w-full">
          <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
          <AnnualConsolidationGridMiniTable
            consol={consol}
            year={year}
            month={month}
            commentMarkersByLineCode={commentMarkersByLineCode}
            checkOpen={checkOpen}
            toggle={toggle}
            onDrill={onDrill}
          />
        </div>

        <div className="min-w-0 w-full flex flex-col">
          <StatementSectionHeading>{KEY_DRIVERS_HEADING}</StatementSectionHeading>
          <StatementNarrativeList
            intro={narrative.intro}
            bullets={bullets}
            loading={narrativeBusy}
            onSelect={setDetailBullet}
          />
        </div>
      </div>
    </div>
    {detailBullet && (
      <PlDetailOverlay
        bullet={detailBullet}
        year={year}
        month={month}
        statement={statement}
        onClose={() => setDetailBullet(null)}
      />
    )}
    </>
  )
}
