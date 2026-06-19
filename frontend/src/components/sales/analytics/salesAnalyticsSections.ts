import type { LucideIcon } from 'lucide-react'
import {
  BarChart3,
  GitBranch,
  Globe2,
  LineChart,
  Package,
  Table2,
  TrendingUp,
  Users,
} from 'lucide-react'

export type SalesAnalyticsSectionId =
  | 'sales-analytics-kpi-charts'
  | 'sales-analytics-metric-bridge'
  | 'sales-analytics-top-orders'
  | 'sales-analytics-top-customers'
  | 'sales-analytics-top-suppliers'
  | 'sales-analytics-geography'
  | 'sales-analytics-breakdown'
  | 'sales-analytics-revenue-trend'
  | 'sales-analytics-dimension-performance'
  | 'sales-analytics-churn'

export type SalesAnalyticsNavItem = {
  id: SalesAnalyticsSectionId
  label: string
  description: string
  icon: LucideIcon
}

export type SalesAnalyticsNavGroup = {
  title: string
  items: SalesAnalyticsNavItem[]
}

export const SALES_ANALYTICS_NAV_GROUPS: SalesAnalyticsNavGroup[] = [
  {
    title: 'Overview',
    items: [
      {
        id: 'sales-analytics-kpi-charts',
        label: 'Trend & composition',
        description: 'Sales development and mix',
        icon: LineChart,
      },
      {
        id: 'sales-analytics-metric-bridge',
        label: 'Metric bridge',
        description: 'Period-over-period drivers',
        icon: GitBranch,
      },
    ],
  },
  {
    title: 'Customers & orders',
    items: [
      {
        id: 'sales-analytics-top-orders',
        label: 'Top orders',
        description: 'Largest invoices',
        icon: Package,
      },
      {
        id: 'sales-analytics-top-customers',
        label: 'Top customers',
        description: 'Revenue tiers',
        icon: Users,
      },
      {
        id: 'sales-analytics-top-suppliers',
        label: 'Top suppliers',
        description: 'Cost concentration',
        icon: Users,
      },
    ],
  },
  {
    title: 'Geography',
    items: [
      {
        id: 'sales-analytics-geography',
        label: 'Revenue map',
        description: 'Countries & locations',
        icon: Globe2,
      },
      {
        id: 'sales-analytics-revenue-trend',
        label: 'Revenue by dimension',
        description: 'Time series breakdown',
        icon: BarChart3,
      },
    ],
  },
  {
    title: 'Profitability',
    items: [
      {
        id: 'sales-analytics-breakdown',
        label: 'Sales breakdown',
        description: 'Hierarchical table',
        icon: Table2,
      },
      {
        id: 'sales-analytics-dimension-performance',
        label: 'Performance by dimension',
        description: 'Actual vs plan chart',
        icon: TrendingUp,
      },
      {
        id: 'sales-analytics-churn',
        label: 'Churn bridge',
        description: 'Retention components',
        icon: GitBranch,
      },
    ],
  },
]

export function scrollToSalesAnalyticsSection(id: SalesAnalyticsSectionId): void {
  const el = document.getElementById(id)
  if (!el) return
  const top = el.getBoundingClientRect().top + window.scrollY - 72
  window.scrollTo({ top, behavior: 'smooth' })
}
