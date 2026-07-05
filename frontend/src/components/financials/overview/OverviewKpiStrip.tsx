import { Banknote, Percent, Timer, TrendingUp } from 'lucide-react'
import KpiCard from '../../cockpit/KpiCard'
import type { OverviewKpiStripItem } from './overviewBriefingUtils'

const ICONS = [TrendingUp, Percent, Banknote, Timer] as const
const ACCENTS = ['#1E3A5F', '#2563EB', '#059669', '#7C3AED'] as const

type Props = {
  items: OverviewKpiStripItem[]
}

export default function OverviewKpiStrip({ items }: Props) {
  if (!items.length) return null

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      {items.map((item, index) => (
        <KpiCard
          key={item.title}
          title={item.title}
          value={item.value}
          deltaPm={item.deltaPm}
          deltaSmly={item.deltaSmly}
          variant={item.variant}
          invertDelta={item.invertDelta}
          icon={ICONS[index % ICONS.length]}
          accentColor={ACCENTS[index % ACCENTS.length]}
          index={index}
          animateEntry={false}
          deltaPmLabel={item.deltaPmLabel}
          deltaSmlyLabel={item.deltaSmlyLabel}
        />
      ))}
    </div>
  )
}
