import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import {
  SALES_ANALYTICS_NAV_GROUPS,
  scrollToSalesAnalyticsSection,
  type SalesAnalyticsSectionId,
} from './salesAnalyticsSections'

const NAV_BRAND = '#1E3A5F'
const NAV_MUTED = '#94A3B8'

type Props = {
  className?: string
}

export default function SalesAnalyticsNav({ className = '' }: Props) {
  const [activeId, setActiveId] = useState<SalesAnalyticsSectionId | null>(null)

  useEffect(() => {
    const ids = SALES_ANALYTICS_NAV_GROUPS.flatMap(g => g.items.map(i => i.id))
    const elements = ids
      .map(id => document.getElementById(id))
      .filter((el): el is HTMLElement => !!el)
    if (!elements.length) return

    const observer = new IntersectionObserver(
      entries => {
        const visible = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)
        if (visible[0]?.target.id) {
          setActiveId(visible[0].target.id as SalesAnalyticsSectionId)
        }
      },
      { rootMargin: '-20% 0px -55% 0px', threshold: [0.08, 0.2, 0.4] },
    )

    elements.forEach(el => observer.observe(el))
    return () => observer.disconnect()
  }, [])

  function handleClick(id: SalesAnalyticsSectionId) {
    setActiveId(id)
    scrollToSalesAnalyticsSection(id)
  }

  return (
    <nav
      className={`rounded-2xl flex flex-col h-full min-h-0 overflow-hidden ${className}`}
      style={{
        background: 'linear-gradient(165deg, #FFFFFF 0%, #F8FAFC 48%, #F1F5F9 100%)',
        border: '1px solid rgba(226, 232, 240, 0.9)',
        boxShadow: '0 4px 24px rgba(30, 58, 95, 0.06), 0 1px 2px rgba(0,0,0,0.04)',
      }}
      aria-label="Analytics sections"
    >
      <div
        className="shrink-0 px-4 pt-4 pb-3 border-b"
        style={{
          borderColor: 'rgba(226, 232, 240, 0.7)',
          background: 'linear-gradient(90deg, rgba(30,58,95,0.04) 0%, transparent 100%)',
        }}
      >
        <p className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: NAV_MUTED }}>
          On this page
        </p>
        <h2 className="text-sm font-semibold mt-0.5 tracking-tight" style={{ color: NAV_BRAND }}>
          Analytics
        </h2>
        <p className="text-[11px] mt-1 leading-snug" style={{ color: '#64748B' }}>
          Jump to any analysis block
        </p>
      </div>

      <div
        className="flex-1 min-h-0 overflow-y-scroll overscroll-contain px-2 py-2 space-y-3"
        style={{ scrollbarGutter: 'stable' }}
      >
        {SALES_ANALYTICS_NAV_GROUPS.map(group => (
          <div key={group.title}>
            <p
              className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider"
              style={{ color: NAV_MUTED }}
            >
              {group.title}
            </p>
            <ul className="space-y-0.5">
              {group.items.map(item => {
                const active = activeId === item.id
                const Icon = item.icon
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => handleClick(item.id)}
                      className="w-full group flex items-start gap-2.5 rounded-xl px-2.5 py-2 text-left transition-all duration-150"
                      style={{
                        background: active ? 'rgba(30, 58, 95, 0.08)' : 'transparent',
                        boxShadow: active ? 'inset 3px 0 0 #1E3A5F' : 'none',
                      }}
                    >
                      <span
                        className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors"
                        style={{
                          background: active ? 'rgba(30, 58, 95, 0.12)' : 'rgba(241, 245, 249, 0.9)',
                          color: active ? NAV_BRAND : '#64748B',
                        }}
                      >
                        <Icon size={15} strokeWidth={1.75} />
                      </span>
                      <span className="min-w-0 flex-1 pt-0.5">
                        <span
                          className="block text-xs font-semibold leading-tight"
                          style={{ color: active ? NAV_BRAND : '#334155' }}
                        >
                          {item.label}
                        </span>
                        <span className="block text-[10px] mt-0.5 leading-snug" style={{ color: NAV_MUTED }}>
                          {item.description}
                        </span>
                      </span>
                      <ChevronRight
                        size={14}
                        className="shrink-0 mt-1.5 opacity-0 group-hover:opacity-60 transition-opacity"
                        style={{ color: NAV_BRAND }}
                      />
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  )
}
