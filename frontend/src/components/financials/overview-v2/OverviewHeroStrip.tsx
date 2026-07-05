/**
 * OverviewHeroStrip — P3 hero KPI strip driven by the batched summary payload.
 *
 * Replaces the P1 briefing-based rendering with data from
 * useOverviewSummaryV2 (one round-trip). 4 cards:
 *   1. Revenue YTD (EUR → displayed in kEUR)  + YoY %
 *   2. EBIT margin %
 *   3. Cash level (EUR → displayed in kEUR)   + Δ YoY
 *   4. Cash conversion cycle (days)
 *
 * SCOPE: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176)
 * and v4 (5178) bundles via IS_OVERVIEW_V2. See overview-v2-redesign-plan.md §5.
 */
import { motion } from 'framer-motion'
import { TrendingUp, Percent, Banknote, Timer } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { OverviewSummaryData } from './hooks/useOverviewSummaryV2'

// ─── Skeleton ─────────────────────────────────────────────────────────────────

function HeroSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="rounded-xl h-[140px] animate-pulse"
          style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
        />
      ))}
    </div>
  )
}

// ─── Formatting helpers (raw-EUR input, displayed as kEUR) ───────────────────

/** Format raw EUR as integer kEUR in German locale (e.g. 5_000_000 → "5.000"). */
function fmtEurAsKeur(eur: number): string {
  const k = Math.round(eur / 1_000)
  const abs = Math.abs(k).toLocaleString('de-DE')
  return eur < 0 ? `(${abs})` : abs
}

/** Format a percentage already multiplied by 100 (e.g. 8.57 → "8,6 %"). */
function fmtPct(v: number): string {
  return v.toLocaleString('de-DE', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }) + '%'
}

/** Format a signed EUR delta compactly as kEUR (e.g. +1_500_000 → "+1.500 k"). */
function fmtEurDelta(eur: number): string {
  const k = Math.round(eur / 1_000)
  const sign = k >= 0 ? '+' : '−'
  return `${sign}${Math.abs(k).toLocaleString('de-DE')} k`
}

// ─── Individual hero card ─────────────────────────────────────────────────────

interface HeroCardProps {
  title: string
  mainValue: string
  unit: string
  delta?: string | null
  /** true = green (favorable up), false = red (unfavorable), null = neutral */
  deltaFav?: boolean | null
  icon: LucideIcon
  accent: string
  index: number
}

function HeroCard({
  title,
  mainValue,
  unit,
  delta,
  deltaFav,
  icon: Icon,
  accent,
  index,
}: HeroCardProps) {
  const deltaColor =
    delta == null
      ? '#94A3B8'
      : deltaFav === true
        ? '#059669'
        : deltaFav === false
          ? '#DC2626'
          : '#64748B'

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.07 }}
      className="rounded-xl p-5 flex flex-col gap-2"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
    >
      {/* Icon + title */}
      <div className="flex items-center gap-2">
        <Icon size={13} aria-hidden style={{ color: accent, flexShrink: 0 }} />
        <span
          className="text-[10px] font-semibold uppercase tracking-wide truncate"
          style={{ color: accent }}
        >
          {title}
        </span>
      </div>

      {/* Main value */}
      <p
        className="text-2xl font-bold tabular-nums leading-none"
        style={{ color: '#111827' }}
      >
        {mainValue}
        <span
          className="text-xs font-normal ml-1.5"
          style={{ color: '#94A3B8' }}
        >
          {unit}
        </span>
      </p>

      {/* Delta row */}
      {delta != null && (
        <p
          className="text-xs tabular-nums"
          style={{ color: deltaColor }}
        >
          {delta}
        </p>
      )}
    </motion.div>
  )
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  summaryData:    OverviewSummaryData | null
  summaryLoading: boolean
  summaryError:   string | null
}

// ─── Component ────────────────────────────────────────────────────────────────

const ACCENTS: readonly string[] = ['#1E3A5F', '#2563EB', '#059669', '#7C3AED']
const ICONS:   readonly LucideIcon[] = [TrendingUp, Percent, Banknote, Timer]

export default function OverviewHeroStrip({
  summaryData,
  summaryLoading,
  summaryError,
}: Props) {
  return (
    <section aria-label="Key performance metrics" className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2
          className="text-xs font-semibold uppercase tracking-widest"
          style={{ color: '#1E3A5F' }}
        >
          Key metrics
        </h2>
        {summaryError && (
          <span className="text-xs" style={{ color: '#DC2626' }}>
            Partial data — {summaryError}
          </span>
        )}
      </div>

      {summaryLoading ? (
        <HeroSkeleton />
      ) : summaryData ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {/* Card 1: Revenue YTD */}
          <HeroCard
            index={0}
            icon={ICONS[0]}
            accent={ACCENTS[0]}
            title="Revenue YTD"
            mainValue={fmtEurAsKeur(summaryData.hero.revenue.ytd)}
            unit="kEUR"
            delta={
              summaryData.hero.revenue.yoy_pct != null
                ? (summaryData.hero.revenue.yoy_pct >= 0 ? '+' : '') +
                  fmtPct(summaryData.hero.revenue.yoy_pct) + ' YoY'
                : null
            }
            deltaFav={
              summaryData.hero.revenue.yoy_pct == null
                ? null
                : summaryData.hero.revenue.yoy_pct >= 0
            }
          />

          {/* Card 2: EBIT margin */}
          <HeroCard
            index={1}
            icon={ICONS[1]}
            accent={ACCENTS[1]}
            title="EBIT margin"
            mainValue={
              summaryData.hero.ebit.margin_pct != null
                ? fmtPct(summaryData.hero.ebit.margin_pct)
                : '—'
            }
            unit="%"
            delta={null}
            deltaFav={null}
          />

          {/* Card 3: Cash level */}
          <HeroCard
            index={2}
            icon={ICONS[2]}
            accent={ACCENTS[2]}
            title="Cash"
            mainValue={fmtEurAsKeur(summaryData.cash.level)}
            unit="kEUR"
            delta={
              summaryData.cash.delta_yoy !== 0
                ? fmtEurDelta(summaryData.cash.delta_yoy) + ' YoY'
                : null
            }
            deltaFav={
              summaryData.cash.delta_yoy === 0
                ? null
                : summaryData.cash.delta_yoy > 0
            }
          />

          {/* Card 4: CCC */}
          <HeroCard
            index={3}
            icon={ICONS[3]}
            accent={ACCENTS[3]}
            title="Cash conv. cycle"
            mainValue={
              summaryData.hero.working_capital.ccc != null
                ? String(Math.round(summaryData.hero.working_capital.ccc))
                : '—'
            }
            unit="days"
            delta={null}
            deltaFav={null}
          />
        </div>
      ) : null}
    </section>
  )
}
