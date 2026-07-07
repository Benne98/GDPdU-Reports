/**
 * Sidebar — persistent left module rail (sits BELOW the full-width AppHeader).
 *
 * - Brand/logo lives in AppHeader; the rail's icon column is centered on the SAME
 *   vertical axis as the header logo (icon centre = 36 px from the left in both the
 *   collapsed 72 px rail and the expanded rail).
 * - Groups: "Main" and "Settings". Project Setup is pinned to the BOTTOM (one-time setup),
 *   separated from the rest.
 * - Collapse toggle at the very bottom: 248 px expanded / 72 px collapsed (icons-only).
 */

import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { motion } from 'framer-motion'
import { PanelLeft, PanelLeftClose } from 'lucide-react'
import { useVisibleModules, type Module } from '../lib/moduleRegistry'

const W_EXPANDED = 248
const W_COLLAPSED = 72 // icon centre at 36 px → aligns with the header logo (paddingLeft 20 + 16)

/** Modules pinned to the bottom of the rail, under the "Setup" heading (in order). */
const BOTTOM_KEYS: string[] = ['project-setup', 'budget']

// ---------------------------------------------------------------------------
// SidebarItem
// ---------------------------------------------------------------------------

function SidebarItem({ module: m, collapsed, index }: { module: Module; collapsed: boolean; index: number }) {
  const [hovered, setHovered] = useState(false)
  const Icon = m.icon

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: 0.03 * index, duration: 0.25, ease: 'easeOut' }}
    >
      <NavLink
        to={m.path}
        title={collapsed ? m.label : undefined}
        end
        className="flex items-center gap-3 rounded-lg text-sm font-medium"
        style={({ isActive }) => ({
          // left pad 19 (+ margin 8 + half icon 9) → icon centre = 36 px, matching the logo + collapsed rail
          padding: collapsed ? '10px 0' : '9px 12px 9px 19px',
          margin: '1px 8px',
          justifyContent: collapsed ? ('center' as const) : undefined,
          color: isActive ? '#1E3A5F' : hovered ? '#1E3A5F' : '#334155',
          background: isActive ? 'rgba(30,58,95,0.09)' : hovered ? 'rgba(30,58,95,0.05)' : 'transparent',
          transition: 'background 0.15s, color 0.15s',
          position: 'relative',
        })}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {({ isActive }) => (
          <>
            {isActive && !collapsed && (
              <span
                aria-hidden
                style={{ position: 'absolute', left: 0, top: 6, bottom: 6, width: 3, borderRadius: 3, background: '#1E3A5F' }}
              />
            )}
            <Icon size={18} aria-hidden style={{ flexShrink: 0 }} />
            {!collapsed && <span className="truncate">{m.label}</span>}
          </>
        )}
      </NavLink>
    </motion.div>
  )
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-4 mb-1.5 text-[10px] font-semibold uppercase tracking-widest" style={{ color: '#64748B' }}>
      {children}
    </p>
  )
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const { all, main, settings } = useVisibleModules()

  const settingsTop = settings.filter((m) => !BOTTOM_KEYS.includes(m.key))
  const bottomModules = BOTTOM_KEYS
    .map((k) => all.find((m) => m.key === k))
    .filter((m): m is Module => Boolean(m))

  return (
    <div
      className="sticky top-16 flex-shrink-0 flex flex-col overflow-y-auto z-30"
      style={{
        width: collapsed ? W_COLLAPSED : W_EXPANDED,
        height: 'calc(100vh - 4rem)',
        background: '#FFFFFF',
        borderRight: '1px solid #E2E8F0',
        boxShadow: '1px 0 4px rgba(0,0,0,0.03)',
        transition: 'width 0.2s ease',
      }}
    >
      {/* Navigation groups */}
      <nav className="flex-1 py-4 overflow-y-auto">
        {main.length > 0 && (
          <div className="mb-2">
            {!collapsed && <GroupLabel>Main</GroupLabel>}
            {main.map((m, i) => (
              <SidebarItem key={m.key} module={m} collapsed={collapsed} index={i} />
            ))}
          </div>
        )}

        {settingsTop.length > 0 && (
          <div className="mt-4">
            {!collapsed && <GroupLabel>Settings</GroupLabel>}
            {settingsTop.map((m, i) => (
              <SidebarItem key={m.key} module={m} collapsed={collapsed} index={main.length + i} />
            ))}
          </div>
        )}
      </nav>

      {/* Setup — Project Setup + Budget Planning, pinned to the bottom (no dividers) */}
      {bottomModules.length > 0 && (
        <div className="pt-2 pb-1 shrink-0">
          {!collapsed && <GroupLabel>Setup</GroupLabel>}
          {bottomModules.map((m, i) => (
            <SidebarItem key={m.key} module={m} collapsed={collapsed} index={i} />
          ))}
        </div>
      )}

      {/* Collapse toggle */}
      <div
        className="flex-shrink-0"
        style={{
          padding: '10px 12px',
          display: 'flex',
          justifyContent: collapsed ? 'center' : 'flex-end',
        }}
      >
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className="rounded-md p-1.5 transition-colors duration-150"
          style={{ color: '#94A3B8' }}
          onMouseEnter={(e) => {
            ;(e.currentTarget as HTMLElement).style.background = 'rgba(30,58,95,0.07)'
            ;(e.currentTarget as HTMLElement).style.color = '#475569'
          }}
          onMouseLeave={(e) => {
            ;(e.currentTarget as HTMLElement).style.background = 'transparent'
            ;(e.currentTarget as HTMLElement).style.color = '#94A3B8'
          }}
        >
          {collapsed ? <PanelLeft size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>
    </div>
  )
}
