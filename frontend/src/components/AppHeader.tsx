/**
 * AppHeader — sticky top navigation bar.
 * Replaces the inline header that was previously in Shell (App.tsx).
 *
 * - Logo always visible (links to /)
 * - Reporting routes: logo left; pages + divider + user + logout grouped on the right
 * - No HealthBadge
 */

import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  BarChart3,
  LayoutDashboard,
  BookOpen,
  Scale,
  RefreshCw,
  Banknote,
  FileText,
  TrendingUp,
  CalendarRange,
  Search,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'

// ---------------------------------------------------------------------------
// Reporting nav definition
// ---------------------------------------------------------------------------

const REPORTING_NAV = [
  { to: '/overview',            label: 'Overview',            Icon: LayoutDashboard },
  { to: '/income-statement',    label: 'Income statement',    Icon: BookOpen        },
  { to: '/balance-sheet',       label: 'Balance sheet',       Icon: Scale           },
  { to: '/working-capital',     label: 'Working capital',     Icon: RefreshCw       },
  { to: '/cash-flow',           label: 'Cash flow',           Icon: Banknote        },
  { to: '/account-statement',   label: 'Export',              Icon: FileText        },
] as const

/** Exact paths that belong to the financial-reporting section.
 *  '/anomaly-detection' is deliberately NOT included: it is reached only via the home
 *  tile and must show an EMPTY page-selection nav (it is not a reporting page). */
const REPORTING_PATHS: ReadonlySet<string> = new Set(REPORTING_NAV.map(n => n.to))

const ANOMALY_NAV = [
  { to: '/anomaly-detection',             label: 'Overview',    Icon: LayoutDashboard },
  { to: '/anomaly-detection/outliers',    label: 'Outliers',    Icon: TrendingUp      },
  { to: '/anomaly-detection/seasonality', label: 'Seasonality', Icon: CalendarRange   },
  { to: '/anomaly-detection/forensic',    label: 'Forensic',    Icon: Search          },
] as const

// ---------------------------------------------------------------------------
// Logo
// ---------------------------------------------------------------------------

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-3 flex-shrink-0 group">
      <div
        className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{ background: '#1E3A5F' }}
      >
        <BarChart3 size={16} style={{ color: '#FFFFFF' }} />
      </div>
      <div className="flex flex-col leading-none">
        <span className="font-bold text-lg tracking-tight" style={{ color: '#1E3A5F' }}>
          Finssentials
        </span>
        <span
          className="font-medium tracking-widest uppercase"
          style={{ color: '#94A3B8', fontSize: '0.6rem' }}
        >
          Financial Intelligence
        </span>
      </div>
    </Link>
  )
}

// ---------------------------------------------------------------------------
// Pill nav item styles
// ---------------------------------------------------------------------------

function pillStyle(isActive: boolean): React.CSSProperties {
  return {
    color:      isActive ? '#1E3A5F' : '#475569',
    background: isActive ? 'rgba(30,58,95,0.08)' : 'transparent',
  }
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
      className="sticky top-0 z-40 bg-white"
      style={{ borderBottom: '1px solid #E2E8F0', boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}
    >
      <div className="max-w-[1680px] mx-auto px-6 lg:px-8 h-16 flex items-center">
        <Logo />
        <div className="flex-1 min-w-0" aria-hidden />

        <div className="flex items-center flex-shrink-0">
          {isReportingRoute && (
            <nav className="flex flex-wrap items-center justify-end gap-0.5">
              {REPORTING_NAV.map(({ to, label, Icon }) => {
                const isActive = location.pathname === to
                return (
                  <NavLink
                    key={to}
                    to={to}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-150"
                    style={({ isActive: navActive }) => pillStyle(navActive || isActive)}
                    onMouseEnter={e => {
                      if (!isActive) {
                        ;(e.currentTarget as HTMLElement).style.color = '#1E3A5F'
                        ;(e.currentTarget as HTMLElement).style.background = 'rgba(30,58,95,0.05)'
                      }
                    }}
                    onMouseLeave={e => {
                      if (!isActive) {
                        ;(e.currentTarget as HTMLElement).style.color = '#475569'
                        ;(e.currentTarget as HTMLElement).style.background = 'transparent'
                      }
                    }}
                  >
                    <Icon size={13} aria-hidden />
                    {label}
                  </NavLink>
                )
              })}
            </nav>
          )}

          {isAnomalyRoute && (
            <nav className="flex flex-wrap items-center justify-end gap-0.5">
              {ANOMALY_NAV.map(({ to, label, Icon }) => {
                const isActive = to === '/anomaly-detection'
                  ? location.pathname === '/anomaly-detection'
                  : location.pathname === to
                return (
                  <NavLink
                    key={to}
                    to={to}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-150"
                    style={() => pillStyle(isActive)}
                    onMouseEnter={e => {
                      if (!isActive) {
                        ;(e.currentTarget as HTMLElement).style.color = '#1E3A5F'
                        ;(e.currentTarget as HTMLElement).style.background = 'rgba(30,58,95,0.05)'
                      }
                    }}
                    onMouseLeave={e => {
                      if (!isActive) {
                        ;(e.currentTarget as HTMLElement).style.color = '#475569'
                        ;(e.currentTarget as HTMLElement).style.background = 'transparent'
                      }
                    }}
                  >
                    <Icon size={13} aria-hidden />
                    {label}
                  </NavLink>
                )
              })}
            </nav>
          )}

          {user && (
            <>
              {(isReportingRoute || isAnomalyRoute) && (
                <div
                  className="mx-4 w-px h-5 flex-shrink-0"
                  style={{ background: '#CBD5E1' }}
                  aria-hidden
                />
              )}

              <span className="text-xs font-medium" style={{ color: '#64748B' }}>
                {user.display_name}
              </span>

              <button
                type="button"
                onClick={handleLogout}
                className="ml-2.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors"
                style={{ borderColor: '#CBD5E1', color: '#1E3A5F' }}
                onMouseEnter={e => {
                  ;(e.currentTarget as HTMLElement).style.background = '#EEF3FA'
                }}
                onMouseLeave={e => {
                  ;(e.currentTarget as HTMLElement).style.background = 'transparent'
                }}
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
