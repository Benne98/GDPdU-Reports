import { Link } from 'react-router-dom'
import { BarChart3 } from 'lucide-react'

const EXPLORE_LINKS = [
  { to: '/overview', label: 'Overview' },
  { to: '/income-statement', label: 'Income statement' },
  { to: '/balance-sheet', label: 'Balance sheet' },
  { to: '/working-capital', label: 'Working capital' },
  { to: '/cash-flow', label: 'Cash flow' },
  { to: '/fdd-bot', label: 'FDD-Bot' },
]

const LEGAL_LINKS = [
  { href: 'http://127.0.0.1:5175/terms', label: 'Terms' },
  { href: 'http://127.0.0.1:5175/privacy', label: 'Privacy' },
  { href: 'http://127.0.0.1:5175/imprint', label: 'Imprint' },
]

export default function GdpduFooter() {
  const year = new Date().getFullYear()

  return (
    <footer
      className="relative pt-16 pb-8"
      style={{ background: '#1E3A5F', borderTop: '1px solid rgba(255,255,255,0.08)' }}
    >
      <div className="max-w-[1480px] mx-auto px-6 lg:px-8">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 gap-10 lg:gap-8 mb-12">
          {/* Brand */}
          <div className="lg:col-span-5">
            <div className="flex items-center gap-3 mb-4 w-fit">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center"
                style={{ background: 'rgba(255,255,255,0.15)' }}
              >
                <BarChart3 size={16} style={{ color: '#FFFFFF' }} />
              </div>
              <span className="font-bold text-lg tracking-tight text-white">Finssentials</span>
            </div>
            <p className="text-xs leading-relaxed max-w-sm text-white/50">
              Financial due diligence &amp; analytics.
            </p>
          </div>

          {/* Connect */}
          <div className="lg:col-span-2">
            <p
              className="font-semibold tracking-widest uppercase mb-4 text-white/40"
              style={{ fontSize: '0.6rem' }}
            >
              Connect
            </p>
            <ul className="flex flex-col gap-2.5">
              <li>
                <span className="text-xs text-white/30 cursor-default">LinkedIn</span>
              </li>
              <li>
                <span className="text-xs text-white/30 cursor-default">finssentials.de</span>
              </li>
            </ul>
          </div>

          {/* Explore */}
          <div className="lg:col-span-3">
            <p
              className="font-semibold tracking-widest uppercase mb-4 text-white/40"
              style={{ fontSize: '0.6rem' }}
            >
              Explore
            </p>
            <ul className="flex flex-col gap-2.5">
              {EXPLORE_LINKS.map(({ to, label }) => (
                <li key={to}>
                  <Link to={to} className="text-xs text-white/70 hover:text-white transition-colors">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          {/* Legal */}
          <div className="lg:col-span-2">
            <p
              className="font-semibold tracking-widest uppercase mb-4 text-white/40"
              style={{ fontSize: '0.6rem' }}
            >
              Legal
            </p>
            <ul className="flex flex-col gap-2.5">
              {LEGAL_LINKS.map(({ href, label }) => (
                <li key={href}>
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-white/70 hover:text-white transition-colors"
                  >
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="pt-6 border-t border-white/10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <p className="text-xs text-white/30">© {year} Finssentials. All rights reserved.</p>
        </div>
      </div>
    </footer>
  )
}
