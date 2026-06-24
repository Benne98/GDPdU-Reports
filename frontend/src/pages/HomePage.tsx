/**
 * HomePage — Module hub / entry point for authenticated users.
 * Shown at `/` after login.  Displays the modules the current user has access to.
 */

import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  LayoutDashboard,
  Bot,
  AlertTriangle,
  Database,
  Users,
  Settings2,
  ArrowRight,
  CalendarRange,
  type LucideIcon,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'

// ---------------------------------------------------------------------------
// ModuleCard — local slim card component (ModuleCard.tsx pattern, no tags)
// ---------------------------------------------------------------------------

interface ModuleCardProps {
  icon:        LucideIcon
  title:       string
  description: string
  path:        string
  index:       number
  /** Internal / Finssentials-staff tile — rendered visually de-emphasised (greyed). */
  muted?:      boolean
}

function ModuleCard({ icon: Icon, title, description, path, index, muted = false }: ModuleCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 28 }}
      animate={{ opacity: muted ? 0.62 : 1, y: 0 }}
      whileHover={{ y: -2, opacity: muted ? 0.85 : 1, transition: { duration: 0.18 } }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1], delay: index * 0.07 }}
      className="group relative flex flex-col rounded-xl overflow-hidden"
      style={{
        background: muted ? '#F8FAFC' : '#FFFFFF',
        border:     muted ? '1px dashed #CBD5E1' : '1px solid #E2E8F0',
        boxShadow:  muted ? 'none' : '0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)',
      }}
    >
      {/* Top accent */}
      <div
        className="absolute top-0 left-0 right-0 h-[2px] rounded-t-xl"
        style={{ background: 'linear-gradient(90deg, transparent, #1E3A5F, transparent)', opacity: 0.5 }}
      />
      {/* Hover glow overlay */}
      <div
        className="absolute inset-0 rounded-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 60% 50% at 50% 0%, rgba(30,58,95,0.04) 0%, transparent 100%)',
          boxShadow:  'inset 0 0 0 1px rgba(30,58,95,0.08)',
        }}
      />

      <Link to={path} className="relative p-6 flex flex-col h-full">
        {/* Icon box */}
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center mb-4"
          style={{
            background: 'rgba(30,58,95,0.07)',
            border:     '1px solid rgba(30,58,95,0.12)',
          }}
        >
          <Icon size={19} style={{ color: '#1E3A5F' }} aria-hidden />
        </div>

        {/* Title */}
        <h2
          className="text-lg font-semibold mb-2 tracking-tight"
          style={{ color: '#111827' }}
        >
          {title}
        </h2>

        {/* Description — always a single concise line */}
        <p className="text-sm leading-relaxed flex-1 line-clamp-1" style={{ color: '#475569' }} title={description}>
          {description}
        </p>

        {/* CTA */}
        <div
          className="inline-flex items-center gap-1.5 text-sm font-semibold mt-5 transition-all duration-200 group/link"
          style={{ color: '#1E3A5F' }}
        >
          Open module
          <ArrowRight
            size={14}
            className="transition-transform duration-200 group-hover:translate-x-1"
            aria-hidden
          />
        </div>
      </Link>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// Module definitions
// ---------------------------------------------------------------------------

type ModuleDef = {
  icon:        LucideIcon
  title:       string
  description: string
  path:        string
  adminOnly?:  boolean
  /** Finssentials-internal setup tool — shown below a divider, separated from end-user modules. */
  staff?:      boolean
}

const MODULES: ModuleDef[] = [
  {
    icon:        LayoutDashboard,
    title:       'Reporting',
    description: 'Financial statements, KPIs and dashboards.',
    path:        '/overview',
  },
  {
    icon:        Bot,
    title:       'FDD-Bot',
    description: 'Chat-based due diligence assistant.',
    path:        '/fdd-bot',
  },
  {
    icon:        AlertTriangle,
    title:       'Anomaly Detection',
    description: 'Material swings, sign flips and balance breaks.',
    path:        '/anomaly-detection',
  },
  {
    icon:        Database,
    title:       'Data Update',
    description: 'Upload accounting data and refresh ledgers.',
    path:        '/ingestion',
    adminOnly:   true,
  },
  {
    icon:        CalendarRange,
    title:       'Budget Planning',
    description: 'Plan BS/PL positions and per Debtor / Creditor.',
    path:        '/budget',
    adminOnly:   true,
  },
  {
    icon:        Users,
    title:       'Role management',
    description: 'Users, roles and page access.',
    path:        '/role-management',
    adminOnly:   true,
  },
  {
    icon:        Settings2,
    title:       'Project Setup',
    description: 'One-time setup: entities, mapping and balances.',
    path:        '/project-setup',
    adminOnly:   true,
    staff:       true,
  },
]

// ---------------------------------------------------------------------------
// HomePage
// ---------------------------------------------------------------------------

export default function HomePage() {
  const { user, isAdmin } = useAuth()

  const visibleModules = MODULES.filter(m => !m.adminOnly || isAdmin)
  const endUserModules = visibleModules.filter(m => !m.staff)
  const staffModules = visibleModules.filter(m => m.staff)

  const greeting = user?.display_name
    ? `Welcome back, ${user.display_name}`
    : 'Welcome to Finssentials'

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="mx-auto w-full max-w-[1680px] px-6 py-10">
        {/* Page header */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-8"
        >
          <p
            className="text-xs font-semibold uppercase tracking-widest mb-2"
            style={{ color: '#1E3A5F' }}
          >
            Finssentials Platform
          </p>
          <h1
            className="text-3xl font-bold tracking-tight"
            style={{ color: '#111827' }}
          >
            {greeting}
          </h1>
          <p className="text-sm mt-1.5" style={{ color: '#94A3B8' }}>
            Choose a module to continue. Your reporting suite, data tools and assistants in one place.
          </p>
        </motion.div>

        {/* End-user module grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {endUserModules.map((mod, i) => (
            <ModuleCard
              key={mod.path}
              icon={mod.icon}
              title={mod.title}
              description={mod.description}
              path={mod.path}
              index={i}
            />
          ))}
        </div>

        {/* Divider + Finssentials-internal area (staff only) */}
        {staffModules.length > 0 && (
          <>
            <div className="flex items-center gap-3 mt-12 mb-6">
              <div className="h-px flex-1" style={{ background: '#E2E8F0' }} />
              <span
                className="text-[11px] font-semibold uppercase tracking-widest whitespace-nowrap"
                style={{ color: '#94A3B8' }}
              >
                For Finssentials staff
              </span>
              <div className="h-px flex-1" style={{ background: '#E2E8F0' }} />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {staffModules.map((mod, i) => (
                <ModuleCard
                  key={mod.path}
                  icon={mod.icon}
                  title={mod.title}
                  description={mod.description}
                  path={mod.path}
                  index={i}
                  muted
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
