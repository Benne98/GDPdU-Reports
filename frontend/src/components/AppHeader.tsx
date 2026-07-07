/**
 * AppHeader — full-width top bar spanning ABOVE the sidebar rail and content.
 *
 * Calm, refined deep-navy bar (recedes so the analyses stay the focus). Crisp text.
 * - Brand/logo on the LEFT — always visible, left edge flush with the sidebar icon column.
 * - Reporting/Anomaly nav pills on the right; reporting pages with sub-pages reveal a
 *   subpage popover on hover (deep-links to /parent?sub=<id>). Right: user + logout.
 */

import { useState } from 'react'
import { NavLink, Link, useLocation, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  BookOpen,
  Scale,
  RefreshCw,
  Banknote,
  FileText,
  TrendingUp,
  CalendarRange,
  Search,
  ChevronDown,
  Check,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'

// ---------------------------------------------------------------------------
// Nav definitions
// ---------------------------------------------------------------------------

type SubPage = { id: string; label: string }

const REPORTING_NAV: { to: string; label: string; Icon: typeof LayoutDashboard; sub?: SubPage[] }[] = [
  { to: '/overview',          label: 'Overview',          Icon: LayoutDashboard },
  { to: '/income-statement',  label: 'Income statement',  Icon: BookOpen, sub: [
    { id: 'pl-statement', label: 'P&L statement' },
    { id: 'profitability', label: 'Profitability' },
    { id: 'payroll', label: 'Payroll' },
  ] },
  { to: '/balance-sheet',     label: 'Balance sheet',     Icon: Scale, sub: [
    { id: 'balance-sheet', label: 'Balance sheet' },
    { id: 'receivables-aging', label: 'Receivables Aging' },
    { id: 'payables-aging', label: 'Payables Aging' },
    { id: 'fixed-assets', label: 'Fixed assets' },
  ] },
  { to: '/working-capital',   label: 'Working capital',   Icon: RefreshCw },
  { to: '/cash-flow',         label: 'Cash flow',         Icon: Banknote, sub: [
    { id: 'cash-flow', label: 'Cash flow statement' },
    { id: 'cash-debt', label: 'Cash & debt' },
  ] },
  { to: '/account-statement', label: 'Export',            Icon: FileText },
]

/** '/anomaly-detection' is intentionally excluded from primary nav (dropped from the rail +
 *  home redirect); ANOMALY_NAV still renders when already on an /anomaly-detection* route. */
const REPORTING_PATHS: ReadonlySet<string> = new Set(REPORTING_NAV.map(n => n.to))

const ANOMALY_NAV = [
  { to: '/anomaly-detection',             label: 'Overview',    Icon: LayoutDashboard },
  { to: '/anomaly-detection/outliers',    label: 'Outliers',    Icon: TrendingUp      },
  { to: '/anomaly-detection/seasonality', label: 'Seasonality', Icon: CalendarRange   },
  { to: '/anomaly-detection/forensic',    label: 'Forensic',    Icon: Search          },
] as const

const IDLE = '#CFDCEC'  // crisp light steel-blue on navy (idle nav) — brightened for legibility

// ---------------------------------------------------------------------------
// Nav pill (+ optional hover subpage popover)
// ---------------------------------------------------------------------------

function NavPill({ to, label, Icon, isActive, sub }: {
  to: string; label: string; Icon: typeof LayoutDashboard; isActive: boolean; sub?: SubPage[]
}) {
  const [open, setOpen] = useState(false)
  const location = useLocation()

  const hasSub = Boolean(sub && sub.length > 0)
  // Which sub-page is currently open — only when we're already on this page.
  // Falls back to the first sub-page, which is the page's default view (no ?sub=).
  const currentSub = new URLSearchParams(location.search).get('sub')
  const activeSubId = isActive ? (currentSub ?? sub?.[0]?.id ?? null) : null

  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <NavLink
        to={to}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold"
        style={{
          color: isActive ? '#FFFFFF' : IDLE,
          background: isActive ? 'rgba(255,255,255,0.11)' : 'transparent',
          border: isActive ? '1px solid rgba(255,255,255,0.16)' : '1px solid transparent',
          transition: 'color 0.15s, background 0.15s',
        }}
        onMouseEnter={e => { if (!isActive) { (e.currentTarget as HTMLElement).style.color = '#FFFFFF'; (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)' } }}
        onMouseLeave={e => { if (!isActive) { (e.currentTarget as HTMLElement).style.color = IDLE; (e.currentTarget as HTMLElement).style.background = 'transparent' } }}
      >
        <Icon size={15} strokeWidth={2.25} aria-hidden />
        {label}
        {hasSub && (
          <ChevronDown
            size={13}
            strokeWidth={2.5}
            aria-hidden
            style={{ marginLeft: 1, opacity: 0.7, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}
          />
        )}
      </NavLink>

      {hasSub && open && (
        <div className="absolute left-0 top-full pt-2 z-50" style={{ minWidth: 236 }}>
          <div
            className="rounded-2xl p-1.5"
            style={{ background: '#FFFFFF', border: '1px solid #E8EDF3', boxShadow: '0 16px 40px rgba(15,30,50,0.16)' }}
          >
            <p className="px-2.5 pt-1.5 pb-1.5 text-[10px] font-semibold uppercase tracking-widest" style={{ color: '#94A3B8' }}>
              {label}
            </p>
            {sub!.map(sp => {
              const active = sp.id === activeSubId
              return (
                <Link
                  key={sp.id}
                  to={`${to}?sub=${sp.id}`}
                  className="flex items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-sm"
                  style={{
                    color: active ? '#1E3A5F' : '#475569',
                    fontWeight: active ? 600 : 500,
                    background: active ? 'rgba(30,58,95,0.08)' : 'transparent',
                    transition: 'background 0.12s, color 0.12s',
                  }}
                  onMouseEnter={e => { if (!active) { (e.currentTarget as HTMLElement).style.background = 'rgba(30,58,95,0.05)'; (e.currentTarget as HTMLElement).style.color = '#1E3A5F' } }}
                  onMouseLeave={e => { if (!active) { (e.currentTarget as HTMLElement).style.background = 'transparent'; (e.currentTarget as HTMLElement).style.color = '#475569' } }}
                >
                  <span className="truncate">{sp.label}</span>
                  {active && <Check size={15} strokeWidth={2.5} style={{ color: '#1E3A5F', flexShrink: 0 }} aria-hidden />}
                </Link>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AppHeader
// ---------------------------------------------------------------------------

export default function AppHeader() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()

  const isReportingRoute = REPORTING_PATHS.has(location.pathname)
  const isAnomalyRoute = location.pathname.startsWith('/anomaly-detection')

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <header
      className="sticky top-0 z-40"
      style={{
        background: 'linear-gradient(180deg, #1E3A5F 0%, #1B3453 100%)',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        boxShadow: '0 1px 8px rgba(15,30,50,0.20)',
        WebkitFontSmoothing: 'antialiased',
        MozOsxFontSmoothing: 'grayscale',
      }}
    >
      {/* left pad 20px = sidebar icon column (item margin 8 + padding 12) → brand is flush with the icons */}
      <div className="h-16 flex items-center gap-6" style={{ paddingLeft: 20, paddingRight: 28 }}>
        {/* Brand lockup — serif "F" monogram │ wordmark + banner, in our navy palette */}
        <Link to="/overview" className="flex items-center gap-3 flex-shrink-0 min-w-0" title="Finssentials — Financial Intelligence">
          {/* 32px-wide centering column: left edge at 20 → glyph centre = 36px, matching the sidebar icon axis */}
          <span className="w-8 flex items-center justify-center flex-shrink-0">
            <svg viewBox="10 0 34 56" height={38} fill="#FFFFFF" role="img" aria-label="Finssentials" style={{ display: 'block' }}>
              <path d="M18 4 H28 V50 H18 Z M18 4 H40 V12 H18 Z M18 24 H35 V31 H18 Z M13 4 H18 V10 H13 Z M37 2 H42 V14 H37 Z M32 22 H37 V33 H32 Z M12 46 H34 V50 H12 Z" />
            </svg>
          </span>
          <span aria-hidden style={{ width: 1, height: 30, background: 'rgba(255,255,255,0.24)', flexShrink: 0 }} />
          <div className="flex flex-col min-w-0" style={{ lineHeight: 1 }}>
            <span style={{ fontSize: 19, fontWeight: 700, color: '#FFFFFF', letterSpacing: '-0.01em' }}>
              Finssentials
            </span>
            <span className="uppercase" style={{ fontSize: '0.5rem', fontWeight: 600, letterSpacing: '0.24em', color: '#A6BBD6', marginTop: 4 }}>
              Financial Intelligence
            </span>
          </div>
        </Link>

        <div className="flex-1 min-w-0" aria-hidden />

        {/* Right group: page nav + account */}
        <div className="flex items-center flex-shrink-0">
          {isReportingRoute && (
            <nav className="flex flex-wrap items-center justify-end gap-0.5">
              {REPORTING_NAV.map(({ to, label, Icon, sub }) => (
                <NavPill key={to} to={to} label={label} Icon={Icon} sub={sub} isActive={location.pathname === to} />
              ))}
            </nav>
          )}

          {isAnomalyRoute && (
            <nav className="flex flex-wrap items-center justify-end gap-0.5">
              {ANOMALY_NAV.map(({ to, label, Icon }) => (
                <NavPill
                  key={to}
                  to={to}
                  label={label}
                  Icon={Icon}
                  isActive={to === '/anomaly-detection'
                    ? location.pathname === '/anomaly-detection'
                    : location.pathname === to}
                />
              ))}
            </nav>
          )}

          {user && (
            <>
              {(isReportingRoute || isAnomalyRoute) && (
                <div className="mx-4 w-px h-5 flex-shrink-0" style={{ background: 'rgba(255,255,255,0.16)' }} aria-hidden />
              )}

              <span className="text-xs font-semibold" style={{ color: '#C6D6E8' }}>
                {user.display_name}
              </span>

              <button
                type="button"
                onClick={handleLogout}
                className="ml-2.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
                style={{ border: '1px solid rgba(255,255,255,0.22)', color: '#FFFFFF', background: 'rgba(255,255,255,0.05)' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.14)' }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.05)' }}
              >
                Log out
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
