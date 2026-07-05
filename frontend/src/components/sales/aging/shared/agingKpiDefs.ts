import { Clock, FileText, Percent, Wallet } from 'lucide-react'
import type { KpiDef } from '../../operational/OperationalKpiGrid'

export type AgingTrendFormat = 'currency' | 'days' | 'count' | 'percent'

export type AgingKpiDef = KpiDef & {
  trendKey: 'gross_balance' | 'balance' | 'overdue' | 'overdue_pct' | 'dso_days' | 'dpo_days' | 'open_documents'
  trendFormat: AgingTrendFormat
}

export const RECEIVABLES_AGING_KPI_DEFS: AgingKpiDef[] = [
  {
    key: 'total_open_gross',
    title: 'Total receivables',
    format: 'currency',
    icon: Wallet,
    accentColor: '#1E3A5F',
    trendKey: 'gross_balance',
    trendFormat: 'currency',
  },
  {
    key: 'overdue_pct',
    title: 'Overdue %',
    format: 'percent',
    icon: Percent,
    accentColor: '#D97706',
    invertDelta: true,
    trendKey: 'overdue_pct',
    trendFormat: 'percent',
  },
  {
    key: 'dso_days',
    title: 'DSO',
    format: 'days',
    icon: Clock,
    accentColor: '#64748B',
    invertDelta: true,
    trendKey: 'dso_days',
    trendFormat: 'days',
  },
  {
    key: 'open_documents',
    title: 'Open invoices',
    format: 'count',
    icon: FileText,
    accentColor: '#475569',
    trendKey: 'open_documents',
    trendFormat: 'count',
  },
]

export const PAYABLES_AGING_KPI_DEFS: AgingKpiDef[] = [
  {
    key: 'total_open_gross',
    title: 'Total payables',
    format: 'currency',
    icon: Wallet,
    accentColor: '#14532D',
    trendKey: 'gross_balance',
    trendFormat: 'currency',
  },
  {
    key: 'overdue_pct',
    title: 'Overdue %',
    format: 'percent',
    icon: Percent,
    accentColor: '#D97706',
    invertDelta: true,
    trendKey: 'overdue_pct',
    trendFormat: 'percent',
  },
  {
    key: 'dpo_days',
    title: 'DPO',
    format: 'days',
    icon: Clock,
    accentColor: '#64748B',
    invertDelta: true,
    trendKey: 'dpo_days',
    trendFormat: 'days',
  },
  {
    key: 'open_documents',
    title: 'Open bills',
    format: 'count',
    icon: FileText,
    accentColor: '#475569',
    trendKey: 'open_documents',
    trendFormat: 'count',
  },
]
