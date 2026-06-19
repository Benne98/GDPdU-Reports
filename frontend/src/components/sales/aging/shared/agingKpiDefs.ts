import { AlertTriangle, Clock, FileText, Wallet } from 'lucide-react'
import type { KpiDef } from '../../operational/OperationalKpiGrid'

export type AgingTrendFormat = 'currency' | 'days' | 'count'

export type AgingKpiDef = KpiDef & {
  trendKey: 'balance' | 'overdue' | 'dso_days' | 'dpo_days' | 'open_documents'
  trendFormat: AgingTrendFormat
}

export const RECEIVABLES_AGING_KPI_DEFS: AgingKpiDef[] = [
  {
    key: 'total_receivables',
    title: 'Total receivables',
    format: 'currency',
    icon: Wallet,
    accentColor: '#1E3A5F',
    trendKey: 'balance',
    trendFormat: 'currency',
  },
  {
    key: 'overdue',
    title: 'Overdue',
    format: 'currency',
    icon: AlertTriangle,
    accentColor: '#F59E0B',
    invertDelta: true,
    trendKey: 'overdue',
    trendFormat: 'currency',
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
    key: 'total_payables',
    title: 'Total payables',
    format: 'currency',
    icon: Wallet,
    accentColor: '#14532D',
    trendKey: 'balance',
    trendFormat: 'currency',
  },
  {
    key: 'overdue',
    title: 'Overdue',
    format: 'currency',
    icon: AlertTriangle,
    accentColor: '#F59E0B',
    invertDelta: true,
    trendKey: 'overdue',
    trendFormat: 'currency',
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
    title: 'Open invoices',
    format: 'count',
    icon: FileText,
    accentColor: '#475569',
    trendKey: 'open_documents',
    trendFormat: 'count',
  },
]
