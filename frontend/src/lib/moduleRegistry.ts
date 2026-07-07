/**
 * moduleRegistry.ts — single source of truth for the module catalogue.
 *
 * A module is visible iff:
 *   1. ENABLED_MODULES.has(m.key)          — customer Baukasten allowlist
 *   2. !m.adminOnly || isAdmin             — role gate
 *   3. page_keys restriction (fail-open):
 *        isAdmin  → always passes (admins bypass page_keys)
 *        !user?.page_keys?.length → no restriction list present = fail-open (passes)
 *        m.pageKey === undefined  → module is not page-restricted (passes)
 *        user.page_keys.includes(m.pageKey) → explicit permission
 */

import { LayoutDashboard, Bot, CalendarRange, Settings2, Database, Users } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { ENABLED_MODULES } from '../config/enabledModules'

// ---------------------------------------------------------------------------
// Module interface
// ---------------------------------------------------------------------------

export interface Module {
  /** Stable module identifier — used in ENABLED_MODULES and page_keys. */
  key: string
  label: string
  description: string
  icon: LucideIcon
  path: string
  group: 'main' | 'settings'
  adminOnly: boolean
  /** Maps to role page_keys; undefined = module is not page-restricted. */
  pageKey?: string
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

export const MODULES: Module[] = [
  {
    key: 'reporting',
    label: 'Reporting',
    description: 'Financial statements, KPIs and dashboards.',
    icon: LayoutDashboard,
    path: '/overview',
    group: 'main',
    adminOnly: false,
    pageKey: 'overview',
  },
  {
    key: 'fdd-bot',
    label: 'FDD-Bot',
    description: 'Chat-based due diligence assistant.',
    icon: Bot,
    path: '/fdd-bot',
    group: 'main',
    adminOnly: false,
    pageKey: 'fdd-bot',
  },
  {
    key: 'budget',
    label: 'Budget Planning',
    description: 'Plan BS/PL positions per Debtor / Creditor.',
    icon: CalendarRange,
    path: '/budget',
    group: 'settings',
    adminOnly: true,
    pageKey: undefined,
  },
  {
    key: 'project-setup',
    label: 'Project Setup',
    description: 'One-time setup: entities, mapping and balances.',
    icon: Settings2,
    path: '/project-setup',
    group: 'settings',
    adminOnly: true,
    pageKey: undefined,
  },
  {
    key: 'ingestion',
    label: 'Data Update',
    description: 'Upload accounting data and refresh ledgers.',
    icon: Database,
    path: '/ingestion',
    group: 'settings',
    adminOnly: true,
    pageKey: 'ingestion',
  },
  {
    key: 'role-management',
    label: 'Role Management',
    description: 'Users, roles and page access.',
    icon: Users,
    path: '/role-management',
    group: 'settings',
    adminOnly: true,
    pageKey: 'role-management',
  },
]

// ---------------------------------------------------------------------------
// useVisibleModules hook
// ---------------------------------------------------------------------------

export interface VisibleModules {
  all: Module[]
  main: Module[]
  settings: Module[]
}

export function useVisibleModules(): VisibleModules {
  const { user, isAdmin } = useAuth()

  const visible = MODULES.filter((m) => {
    // 1. Customer Baukasten allowlist
    if (!ENABLED_MODULES.has(m.key)) return false

    // 2. Admin gate
    if (m.adminOnly && !isAdmin) return false

    // 3. page_keys restriction (fail-open when absent/empty)
    if (!isAdmin && user?.page_keys?.length && m.pageKey !== undefined) {
      if (!user.page_keys.includes(m.pageKey)) return false
    }

    return true
  })

  return {
    all: visible,
    main: visible.filter((m) => m.group === 'main'),
    settings: visible.filter((m) => m.group === 'settings'),
  }
}
