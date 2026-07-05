/**
 * CashLiquidityBlock — Block (2): Cash development & liquidity.
 * P5: real content driven by GET /api/v1/financials/overview/liquidity.
 *
 * Renders:
 *   1. Cash level headline (signed; overdraft displayed tastefully, not garish).
 *   2. Liquidity bridge: Cash + CollectibleAR − OutstandingAP = LiquidityAvailable.
 *   3. AR haircut breakdown by aging band (raw → collectible; haircut%; credit_flag subtly).
 *   4. Up to 3 one-liner findings deep-linking to /cash-flow.
 *
 * All amounts already in kEUR from the endpoint (meta.unit = 'kEUR').
 * Palette: muted tones only — no garish red/green blast. Overdraft/negative cash
 * is marked clearly via parentheses notation + amber accent (#B45309).
 * Favorable outcome uses dark navy (#1E3A5F), not garish green.
 *
 * Liquidity bridge identity (confirmed from backend contract):
 *   liquidity_available = cash + collectible_ar − outstanding_ap
 *
 * Scope: reporting-v2 / port 5177 only — tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via IS_OVERVIEW_V2 mode gate. See overview-v2-redesign-plan.md §5.
 */
import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, AlertTriangle, Info } from 'lucide-react'
import OverviewAnalysisBlock from '../OverviewAnalysisBlock'
import type { OverviewLiquidityData, LiquidityArBand } from '../hooks/useOverviewLiquidityV2'

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  data:    OverviewLiquidityData | null
  loading: boolean
  error:   string | null
}

// ─── kEUR formatting ──────────────────────────────────────────────────────────

/**
 * Format an integer kEUR value for display.
 * Negative → accounting parentheses: (85) — not a leading minus sign.
 * Uses German locale thousands separator: 1234 → "1.234".
 */
function fmtKeur(keur: number): string {
  const isNeg = keur < 0
  const s = Math.round(Math.abs(keur)).toLocaleString('de-DE')
  return isNeg ? `(${s})` : s
}

/** Format a haircut factor as a percentage string: 0.25 → "25 %" */
function fmtHaircut(h: number): string {
  return Math.round(h * 100).toLocaleString('de-DE') + ' %'
}

// ─── Color helpers (muted palette) ───────────────────────────────────────────

/**
 * Return a signed-amount color.
 * Negative → amber #B45309 (clear warning, not garish red).
 * Zero     → slate  #94A3B8.
 * Positive → navy   #1E3A5F (restrained; no garish green).
 */
function signedColor(value: number): string {
  if (value < 0) return '#B45309'
  if (value === 0) return '#94A3B8'
  return '#1E3A5F'
}

// ─── Cash level badge ─────────────────────────────────────────────────────────

function CashBadge({ cash }: { cash: number }) {
  const isOverdraft = cash < 0
  const color = signedColor(cash)
  return (
    <div
      className="flex items-baseline gap-2 rounded-lg px-4 py-3"
      style={{
        background: isOverdraft ? 'rgba(180,83,9,0.06)' : '#F8FAFC',
        border: `1px solid ${isOverdraft ? 'rgba(180,83,9,0.20)' : '#E2E8F0'}`,
      }}
    >
      <span
        className="text-[10px] font-semibold uppercase tracking-wide shrink-0"
        style={{ color: '#94A3B8' }}
      >
        {isOverdraft ? 'Overdraft' : 'Cash'}
      </span>
      <span
        className="text-xl font-bold tabular-nums"
        style={{ color }}
      >
        {fmtKeur(cash)}
      </span>
      <span className="text-xs" style={{ color: '#94A3B8' }}>
        kEUR
      </span>
      {isOverdraft && (
        <span
          className="ml-auto text-[10px] font-medium"
          style={{ color: '#B45309' }}
        >
          negative
        </span>
      )}
    </div>
  )
}

// ─── Liquidity bridge row ─────────────────────────────────────────────────────

interface BridgeRowProps {
  /** Arithmetic prefix shown before the label: "", "+", "−", "=". */
  prefix: string
  label: string
  /**
   * Amount to display in kEUR.
   * For Outstanding AP pass the raw positive value — the "−" prefix is visual-only.
   */
  value: number
  /**
   * When true the amount color follows the sign (navy/amber).
   * False = neutral grey. Default false.
   */
  signed?: boolean
  /** Render the summary divider above and bold text. Default false. */
  isSummary?: boolean
}

function BridgeRow({
  prefix,
  label,
  value,
  signed = false,
  isSummary = false,
}: BridgeRowProps) {
  const amtColor = signed ? signedColor(value) : '#374151'
  return (
    <div
      className="flex items-center justify-between py-1.5"
      style={
        isSummary
          ? { borderTop: '2px solid #E2E8F0', paddingTop: '8px', marginTop: '4px' }
          : { borderBottom: '1px solid #F8FAFC' }
      }
    >
      <div className="flex items-center gap-1.5 min-w-0">
        <span
          className="text-xs tabular-nums shrink-0 w-3 text-center select-none"
          style={{ color: '#CBD5E1' }}
        >
          {prefix}
        </span>
        <span
          className={`text-xs truncate ${isSummary ? 'font-semibold' : 'font-normal'}`}
          style={{ color: isSummary ? amtColor : '#374151' }}
        >
          {label}
        </span>
      </div>
      <span
        className={`text-xs tabular-nums shrink-0 ml-2 ${isSummary ? 'font-semibold' : 'font-normal'}`}
        style={{ color: amtColor }}
      >
        {fmtKeur(value)}&thinsp;kEUR
      </span>
    </div>
  )
}

// ─── AR band row ──────────────────────────────────────────────────────────────

function ArBandRow({ band }: { band: LiquidityArBand }) {
  const hasHaircut = band.haircut > 0
  return (
    <div
      className="flex items-center justify-between py-1"
      style={{ borderBottom: '1px solid #F8FAFC' }}
    >
      {/* Label + optional credit-balance tag */}
      <div className="flex items-center gap-1.5 min-w-0 flex-1 pr-2">
        <span className="text-xs truncate" style={{ color: '#374151' }}>
          {band.label}
        </span>
        {band.credit_flag && (
          <span
            className="text-[9px] px-1 rounded shrink-0 leading-tight"
            style={{
              background: '#F1F5F9',
              color: '#94A3B8',
              border: '1px solid #E2E8F0',
            }}
          >
            credit
          </span>
        )}
      </div>

      {/* Raw · Haircut · Collectible — fixed-width columns */}
      <div className="flex items-center gap-3 shrink-0">
        <span
          className="text-[10px] tabular-nums text-right w-11"
          style={{ color: '#94A3B8' }}
        >
          {fmtKeur(band.raw)}
        </span>
        {hasHaircut ? (
          <span
            className="text-[10px] tabular-nums text-right w-9"
            style={{ color: '#B45309' }}
          >
            {fmtHaircut(band.haircut)}
          </span>
        ) : (
          <span
            className="text-[10px] tabular-nums text-right w-9"
            style={{ color: '#CBD5E1' }}
          >
            —
          </span>
        )}
        <span
          className="text-[10px] font-medium tabular-nums text-right w-11"
          style={{ color: '#1E3A5F' }}
        >
          {fmtKeur(band.collectible)}
        </span>
      </div>
    </div>
  )
}

// ─── Findings ─────────────────────────────────────────────────────────────────

interface Finding {
  text: string
  route: '/cash-flow'
  kind: 'info' | 'warning'
}

/**
 * Build up to 3 one-liner findings from the liquidity payload.
 * Priority order: overdraft/tight-cash → high-haircut AR → net liquidity note.
 */
function buildLiquidityFindings(d: OverviewLiquidityData): Finding[] {
  const route = '/cash-flow' as const
  const findings: Finding[] = []

  // Finding 1: cash position
  if (d.cash < 0) {
    findings.push({
      text: `Cash overdraft of ${fmtKeur(Math.abs(d.cash))} kEUR — review short-term credit lines and payment timing.`,
      route,
      kind: 'warning',
    })
  } else if (d.outstanding_ap > 0 && d.cash < d.outstanding_ap * 0.5) {
    const coverPct = Math.round((d.cash / d.outstanding_ap) * 100)
    findings.push({
      text: `Cash of ${fmtKeur(d.cash)} kEUR covers only ${coverPct} % of outstanding payables — monitor the liquidity buffer.`,
      route,
      kind: 'warning',
    })
  }

  // Finding 2: high-haircut AR bands (91+ days overdue)
  if (findings.length < 3 && d.raw_ar > 0) {
    const highHaircut = d.ar_bands.filter(b => b.haircut >= 0.5)
    const highRaw = highHaircut.reduce((s, b) => s + b.raw, 0)
    if (highRaw > 0) {
      const pct = Math.round((highRaw / d.raw_ar) * 100)
      if (pct >= 5) {
        findings.push({
          text: `${pct} % of AR (${fmtKeur(highRaw)} kEUR) is 91+ days overdue — collectibility risk; review aged debtors on the cash-flow page.`,
          route,
          kind: 'warning',
        })
      }
    }
  }

  // Finding 3: net liquidity and haircut impact
  if (findings.length < 3) {
    const haircutLoss = d.raw_ar - d.collectible_ar
    if (d.liquidity_available < 0) {
      findings.push({
        text: `Net liquidity available is negative (${fmtKeur(d.liquidity_available)} kEUR) — payables exceed cash and collectible receivables.`,
        route,
        kind: 'warning',
      })
    } else if (haircutLoss > 0 && d.raw_ar > 0) {
      const lossPct = Math.round((haircutLoss / d.raw_ar) * 100)
      findings.push({
        text: `AR aging haircut reduces receivables by ${fmtKeur(haircutLoss)} kEUR (${lossPct} %) — liquidity available ${fmtKeur(d.liquidity_available)} kEUR.`,
        route,
        kind: 'info',
      })
    }
  }

  return findings.slice(0, 3)
}

function FindingChip({ finding }: { finding: Finding }) {
  const navigate = useNavigate()
  const Icon = finding.kind === 'warning' ? AlertTriangle : Info
  const accent = finding.kind === 'warning' ? '#B45309' : '#1E3A5F'

  function handleActivate() {
    navigate(finding.route)
  }

  return (
    <div
      className="flex items-start gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors hover:bg-slate-50"
      style={{ background: '#F8FAFC', border: '1px solid #E2E8F0' }}
      role="button"
      tabIndex={0}
      onClick={handleActivate}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') handleActivate()
      }}
      aria-label={finding.text}
    >
      <Icon
        size={11}
        aria-hidden
        style={{ color: accent, flexShrink: 0, marginTop: 2 }}
      />
      <p
        className="flex-1 text-[11px] leading-snug"
        style={{ color: '#374151' }}
      >
        {finding.text}
      </p>
      <ArrowRight
        size={10}
        aria-hidden
        style={{ color: '#CBD5E1', flexShrink: 0, marginTop: 2 }}
      />
    </div>
  )
}

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="space-y-3 animate-pulse">
      {/* Cash badge */}
      <div className="h-11 rounded-lg" style={{ background: '#F1F5F9' }} />
      {/* Bridge label */}
      <div className="h-3 w-24 rounded" style={{ background: '#F1F5F9' }} />
      {/* Bridge rows */}
      <div
        className="rounded-lg px-3 py-1 space-y-1.5"
        style={{ background: '#F8FAFC', border: '1px solid #F1F5F9' }}
      >
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-5 rounded" style={{ background: '#F1F5F9' }} />
        ))}
      </div>
      {/* Band table label */}
      <div className="h-3 w-28 rounded" style={{ background: '#F1F5F9' }} />
      {/* Band rows */}
      <div className="space-y-1">
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <div key={i} className="h-5 rounded" style={{ background: '#F1F5F9' }} />
        ))}
      </div>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function CashLiquidityBlock({ data, loading, error }: Props) {
  const findings = useMemo(
    () => (data ? buildLiquidityFindings(data) : []),
    [data],
  )

  return (
    <OverviewAnalysisBlock title="Cash & Liquidity" deepLink="/cash-flow" compact>
      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          {error}
        </p>
      ) : !data ? (
        <p className="text-xs py-6 text-center" style={{ color: '#94A3B8' }}>
          No liquidity data available for this period.
        </p>
      ) : (
        <div className="space-y-4">

          {/* ── 1. Cash level badge ───────────────────────────────────── */}
          <CashBadge cash={data.cash} />

          {/* ── 2. Liquidity bridge ───────────────────────────────────── */}
          <div>
            <span
              className="text-[10px] font-semibold uppercase tracking-wide block mb-1.5"
              style={{ color: '#94A3B8' }}
            >
              Liquidity bridge
            </span>
            <div
              className="rounded-lg px-3 py-1"
              style={{ background: '#F8FAFC', border: '1px solid #F1F5F9' }}
            >
              {/* Cash (signed — overdraft may be negative) */}
              <BridgeRow
                prefix=""
                label="Cash"
                value={data.cash}
                signed
              />
              {/* + Collectible AR (always positive) */}
              <BridgeRow
                prefix="+"
                label="Collectible AR"
                value={data.collectible_ar}
              />
              {/*
               * − Outstanding AP: value is the raw positive AP amount.
               * The "−" prefix signals the deduction visually.
               * Do NOT negate the value — fmtKeur(positive) shows no parens.
               */}
              <BridgeRow
                prefix="−"
                label="Outstanding AP"
                value={data.outstanding_ap}
              />
              {/* = Available (signed — negative is unfavorable) */}
              <BridgeRow
                prefix="="
                label="Available"
                value={data.liquidity_available}
                signed
                isSummary
              />
            </div>
          </div>

          {/* ── 3. AR haircut breakdown by aging band ────────────────── */}
          {data.ar_bands.length > 0 && (
            <div>
              <div
                className="flex items-center justify-between mb-1.5"
              >
                <span
                  className="text-[10px] font-semibold uppercase tracking-wide"
                  style={{ color: '#94A3B8' }}
                >
                  AR aging &amp; haircut
                </span>
                <span className="text-[10px]" style={{ color: '#CBD5E1' }}>
                  Raw&thinsp;·&thinsp;Haircut&thinsp;·&thinsp;Net
                </span>
              </div>

              <div>
                {data.ar_bands.map((band) => (
                  <ArBandRow key={band.band} band={band} />
                ))}
              </div>

              {/* Totals row */}
              <div
                className="flex items-center justify-between pt-1.5 mt-0.5"
                style={{ borderTop: '1px solid #E2E8F0' }}
              >
                <span
                  className="text-[10px] font-semibold"
                  style={{ color: '#374151' }}
                >
                  Total
                </span>
                <div className="flex items-center gap-3 shrink-0">
                  <span
                    className="text-[10px] font-medium tabular-nums text-right w-11"
                    style={{ color: '#94A3B8' }}
                  >
                    {fmtKeur(data.raw_ar)}
                  </span>
                  {/* Haircut column spacer */}
                  <span className="w-9" />
                  <span
                    className="text-[10px] font-semibold tabular-nums text-right w-11"
                    style={{ color: '#1E3A5F' }}
                  >
                    {fmtKeur(data.collectible_ar)}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* ── 4. Findings (up to 3) ─────────────────────────────────── */}
          {findings.length > 0 && (
            <div className="space-y-1.5">
              {findings.map((f, i) => (
                <FindingChip key={i} finding={f} />
              ))}
            </div>
          )}

        </div>
      )}
    </OverviewAnalysisBlock>
  )
}
