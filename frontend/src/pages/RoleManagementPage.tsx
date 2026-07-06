/**
 * RoleManagementPage — Admin-only page for managing roles and user assignments.
 *
 * Tabs:
 *   Roles  — create/edit/delete roles with page-group checkboxes + entity visibility
 *   Users  — assign roles, toggle is_admin / is_active per user
 *
 * Graceful degradation: if the admin backend endpoints return 404/error, the page
 * shows a preview banner and works with sample data so the design is always visible.
 */

import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ShieldCheck,
  Users,
  Plus,
  Trash2,
  Save,
  ChevronRight,
  AlertCircle,
  CheckSquare,
  Square,
  X,
  Loader2,
} from 'lucide-react'
import { api, type AdminPage, type AdminRole, type AdminUser, type Entity } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import PageShell from '../components/ui/PageShell'

// ─── Design tokens ────────────────────────────────────────────────────────────

const NAVY   = '#1E3A5F'
const BORDER = '#E2E8F0'
const TEXT1  = '#111827'
const TEXT2  = '#475569'
const TEXT3  = '#94A3B8'
const SURF   = '#F4F6F9'

// ─── Static page definitions (fallback when backend is not yet connected) ─────

const FALLBACK_PAGES: AdminPage[] = [
  { key: 'overview',          label: 'Overview',          group: 'reporting' },
  { key: 'income-statement',  label: 'Income statement',  group: 'reporting' },
  { key: 'balance-sheet',     label: 'Balance sheet',     group: 'reporting' },
  { key: 'working-capital',   label: 'Working capital',   group: 'reporting' },
  { key: 'cash-flow',         label: 'Cash flow',         group: 'reporting' },
  { key: 'account-statement', label: 'Export', group: 'reporting' },
  { key: 'fdd-bot',           label: 'FDD-Bot',           group: 'tools'     },
  { key: 'ingestion',         label: 'Data Update',       group: 'tools'     },
  { key: 'plan',              label: 'Plan / Forecast',   group: 'tools'     },
  { key: 'role-management',   label: 'Role management',   group: 'admin'     },
]

const FALLBACK_ROLES: AdminRole[] = [
  {
    role_id:      1,
    role_name:    'Analyst',
    description:  'Read-only access to all reporting modules.',
    page_keys:    ['overview', 'income-statement', 'balance-sheet', 'working-capital', 'cash-flow', 'account-statement'],
    entity_codes: [],
  },
  {
    role_id:      2,
    role_name:    'Manager',
    description:  'Full access to reporting and tools.',
    page_keys:    ['overview', 'income-statement', 'balance-sheet', 'working-capital', 'cash-flow', 'account-statement', 'fdd-bot', 'ingestion', 'plan'],
    entity_codes: [],
  },
  {
    role_id:      3,
    role_name:    'Read-only',
    description:  'Access limited to selected entities.',
    page_keys:    ['overview', 'income-statement'],
    entity_codes: ['DE01', 'AT01'],
  },
]

const GROUP_LABELS: Record<AdminPage['group'], string> = {
  reporting: 'Reporting',
  tools:     'Tools',
  admin:     'Administration',
}

// ─── Toggle switch ─────────────────────────────────────────────────────────────

function Toggle({
  checked,
  onChange,
  disabled = false,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 focus:outline-none focus-visible:ring-2"
      style={{
        background:  checked ? NAVY : '#CBD5E1',
        cursor:      disabled ? 'not-allowed' : 'pointer',
        opacity:     disabled ? 0.5 : 1,
      }}
    >
      <span
        className="inline-block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200"
        style={{
          transform: checked ? 'translate(18px, 2px)' : 'translate(2px, 2px)',
        }}
      />
    </button>
  )
}

// ─── Checkbox ─────────────────────────────────────────────────────────────────

function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex items-center gap-2 text-sm py-0.5 group"
      style={{ color: TEXT2 }}
    >
      {checked
        ? <CheckSquare size={15} style={{ color: NAVY }} />
        : <Square      size={15} style={{ color: '#CBD5E1' }} className="group-hover:text-slate-400" />
      }
      <span>{label}</span>
    </button>
  )
}

// ─── Sub-tab bar (same style as StatementsPage) ────────────────────────────────

type Tab = 'roles' | 'users'

function SubTabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string; Icon: typeof ShieldCheck }[] = [
    { id: 'roles', label: 'Roles',  Icon: ShieldCheck },
    { id: 'users', label: 'Users',  Icon: Users       },
  ]
  return (
    <div
      className="flex items-center gap-0.5 p-1 rounded-lg"
      style={{ background: 'rgba(30,58,95,0.06)', width: 'fit-content' }}
    >
      {tabs.map(({ id, label, Icon }) => {
        const isActive = active === id
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange(id)}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-sm font-medium transition-all duration-150"
            style={{
              color:      isActive ? NAVY : TEXT2,
              background: isActive ? '#FFFFFF' : 'transparent',
              boxShadow:  isActive ? '0 1px 3px rgba(0,0,0,0.10)' : 'none',
            }}
          >
            <Icon size={13} aria-hidden />
            {label}
          </button>
        )
      })}
    </div>
  )
}

// ─── Preview banner (graceful degradation) ────────────────────────────────────

function PreviewBanner() {
  return (
    <div
      className="flex items-center gap-3 rounded-xl px-4 py-3 text-sm mb-6"
      style={{ background: '#FFF7ED', border: '1px solid #FED7AA', color: '#9A3412' }}
    >
      <AlertCircle size={15} className="flex-shrink-0" />
      <span>
        <span className="font-semibold">Role management backend not connected yet</span>
        {' '}— showing a preview with sample data. Connect the backend to enable live editing.
      </span>
    </div>
  )
}

// ─── Page-group checkboxes ─────────────────────────────────────────────────────

function PageGroupSection({
  group,
  pages,
  selectedKeys,
  onChange,
}: {
  group: AdminPage['group']
  pages: AdminPage[]
  selectedKeys: Set<string>
  onChange: (keys: Set<string>) => void
}) {
  const allSelected = pages.every(p => selectedKeys.has(p.key))
  const noneSelected = pages.every(p => !selectedKeys.has(p.key))

  function toggleAll() {
    const next = new Set(selectedKeys)
    if (allSelected) {
      pages.forEach(p => next.delete(p.key))
    } else {
      pages.forEach(p => next.add(p.key))
    }
    onChange(next)
  }

  function toggleOne(key: string, val: boolean) {
    const next = new Set(selectedKeys)
    val ? next.add(key) : next.delete(key)
    onChange(next)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: NAVY }}>
          {GROUP_LABELS[group]}
        </span>
        <button
          type="button"
          onClick={toggleAll}
          className="text-xs font-medium transition-colors"
          style={{ color: allSelected ? TEXT3 : NAVY }}
        >
          {allSelected ? 'Deselect all' : noneSelected ? 'Select all' : 'Select all'}
        </button>
      </div>
      <div className="flex flex-col gap-1 pl-1">
        {pages.map(p => (
          <Checkbox
            key={p.key}
            checked={selectedKeys.has(p.key)}
            onChange={v => toggleOne(p.key, v)}
            label={p.label}
          />
        ))}
      </div>
    </div>
  )
}

// ─── Entity visibility section ─────────────────────────────────────────────────

function EntitySection({
  entities,
  selectedCodes,
  onChange,
}: {
  entities: Entity[]
  selectedCodes: string[]
  onChange: (codes: string[]) => void
}) {
  const allVisible = selectedCodes.length === 0

  function handleAllToggle(val: boolean) {
    onChange(val ? [] : entities.slice(0, 1).map(e => e.legal_entity_code))
  }

  function toggleEntity(code: string, val: boolean) {
    const next = new Set(selectedCodes)
    val ? next.add(code) : next.delete(code)
    const arr = [...next]
    // empty = all visible
    onChange(arr.length === entities.length ? [] : arr)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: NAVY }}>
          Visible entities
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: TEXT3 }}>All entities</span>
          <Toggle
            checked={allVisible}
            onChange={handleAllToggle}
          />
        </div>
      </div>
      {!allVisible && (
        <div className="flex flex-col gap-1 pl-1 mt-2">
          {entities.map(e => (
            <Checkbox
              key={e.legal_entity_code}
              checked={selectedCodes.includes(e.legal_entity_code)}
              onChange={v => toggleEntity(e.legal_entity_code, v)}
              label={e.entity_name}
            />
          ))}
        </div>
      )}
      {allVisible && (
        <p className="text-xs pl-1 mt-1" style={{ color: TEXT3 }}>
          All {entities.length} entities are visible. Toggle off to restrict access.
        </p>
      )}
    </div>
  )
}

// ─── Role editor panel ─────────────────────────────────────────────────────────

interface RoleEditorProps {
  role: AdminRole | null   // null = new role
  pages: AdminPage[]
  entities: Entity[]
  onSave: (data: Omit<AdminRole, 'role_id'>) => Promise<void>
  onDelete?: () => Promise<void>
  onClose: () => void
  saving: boolean
  deleting: boolean
  isPreview: boolean
}

function RoleEditor({
  role,
  pages,
  entities,
  onSave,
  onDelete,
  onClose,
  saving,
  deleting,
  isPreview,
}: RoleEditorProps) {
  const [roleName,    setRoleName]    = useState(role?.role_name   ?? '')
  const [description, setDescription] = useState(role?.description ?? '')
  const [pageKeys,    setPageKeys]    = useState<Set<string>>(new Set(role?.page_keys ?? []))
  const [entityCodes, setEntityCodes] = useState<string[]>(role?.entity_codes ?? [])

  const groups = ['reporting', 'tools', 'admin'] as const
  const pagesByGroup = (g: AdminPage['group']) => pages.filter(p => p.group === g)

  async function handleSave() {
    await onSave({
      role_name:    roleName.trim(),
      description:  description.trim(),
      page_keys:    [...pageKeys],
      entity_codes: entityCodes,
    })
  }

  return (
    <div
      className="flex flex-col h-full rounded-2xl overflow-hidden"
      style={{ background: '#FFFFFF', border: `1px solid ${BORDER}`, boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-5 py-4"
        style={{ borderBottom: `1px solid ${BORDER}` }}
      >
        <span className="text-sm font-semibold" style={{ color: TEXT1 }}>
          {role ? 'Edit role' : 'New role'}
        </span>
        <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600 transition-colors">
          <X size={16} />
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-5">
        {/* Name + description */}
        <div className="flex flex-col gap-3">
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider block mb-1" style={{ color: NAVY }}>
              Role name
            </label>
            <input
              type="text"
              value={roleName}
              onChange={e => setRoleName(e.target.value)}
              placeholder="e.g. Analyst"
              className="w-full rounded-lg px-3 py-2 text-sm outline-none transition-all"
              style={{
                border:     `1px solid ${BORDER}`,
                background: SURF,
                color:      TEXT1,
              }}
              onFocus={e => { e.currentTarget.style.borderColor = NAVY }}
              onBlur={e  => { e.currentTarget.style.borderColor = BORDER }}
            />
          </div>
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider block mb-1" style={{ color: NAVY }}>
              Description
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={2}
              placeholder="Brief description of this role's purpose…"
              className="w-full rounded-lg px-3 py-2 text-sm outline-none resize-none transition-all"
              style={{
                border:     `1px solid ${BORDER}`,
                background: SURF,
                color:      TEXT1,
              }}
              onFocus={e => { e.currentTarget.style.borderColor = NAVY }}
              onBlur={e  => { e.currentTarget.style.borderColor = BORDER }}
            />
          </div>
        </div>

        {/* Page visibility */}
        <div
          className="rounded-xl p-4 flex flex-col gap-4"
          style={{ background: SURF, border: `1px solid ${BORDER}` }}
        >
          <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: TEXT2 }}>
            Visible pages / modules
          </p>
          {groups.map(g => {
            const grpPages = pagesByGroup(g)
            if (!grpPages.length) return null
            return (
              <PageGroupSection
                key={g}
                group={g}
                pages={grpPages}
                selectedKeys={pageKeys}
                onChange={setPageKeys}
              />
            )
          })}
        </div>

        {/* Entity visibility */}
        <div
          className="rounded-xl p-4"
          style={{ background: SURF, border: `1px solid ${BORDER}` }}
        >
          <EntitySection
            entities={entities}
            selectedCodes={entityCodes}
            onChange={setEntityCodes}
          />
        </div>
      </div>

      {/* Footer actions */}
      <div
        className="px-5 py-4 flex items-center justify-between gap-3"
        style={{ borderTop: `1px solid ${BORDER}` }}
      >
        {/* Delete — only for existing roles */}
        <div>
          {role && onDelete && (
            <button
              type="button"
              onClick={onDelete}
              disabled={deleting || isPreview}
              className="flex items-center gap-1.5 text-sm font-medium transition-colors rounded-lg px-3 py-1.5"
              style={{
                color:   '#DC2626',
                border:  '1px solid #FECACA',
                opacity: (deleting || isPreview) ? 0.6 : 1,
                cursor:  (deleting || isPreview) ? 'not-allowed' : 'pointer',
              }}
            >
              {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
              Delete role
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !roleName.trim() || isPreview}
          className="flex items-center gap-1.5 text-sm font-semibold rounded-lg px-4 py-1.5 text-white transition-opacity"
          style={{
            background: NAVY,
            opacity:    (saving || !roleName.trim() || isPreview) ? 0.6 : 1,
            cursor:     (saving || !roleName.trim() || isPreview) ? 'not-allowed' : 'pointer',
          }}
        >
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          Save
        </button>
      </div>
    </div>
  )
}

// ─── Roles tab ─────────────────────────────────────────────────────────────────

function RolesTab({
  roles,
  pages,
  entities,
  isPreview,
  onRefresh,
}: {
  roles:     AdminRole[]
  pages:     AdminPage[]
  entities:  Entity[]
  isPreview: boolean
  onRefresh: () => void
}) {
  const [selected,  setSelected]  = useState<AdminRole | null | 'new'>(null)
  const [saving,    setSaving]    = useState(false)
  const [deleting,  setDeleting]  = useState(false)
  const [localMsg,  setLocalMsg]  = useState<string | null>(null)

  function showMsg(m: string) {
    setLocalMsg(m)
    setTimeout(() => setLocalMsg(null), 3000)
  }

  async function handleSave(data: Omit<AdminRole, 'role_id'>) {
    setSaving(true)
    try {
      if (selected === 'new') {
        await api.adminCreateRole(data)
        showMsg('Role created.')
      } else if (selected) {
        await api.adminUpdateRole(selected.role_id, data)
        showMsg('Role updated.')
      }
      setSelected(null)
      onRefresh()
    } catch (err) {
      showMsg(err instanceof Error ? err.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (selected === null || selected === 'new') return
    if (!window.confirm(`Delete role "${selected.role_name}"? This cannot be undone.`)) return
    setDeleting(true)
    try {
      await api.adminDeleteRole(selected.role_id)
      showMsg('Role deleted.')
      setSelected(null)
      onRefresh()
    } catch (err) {
      showMsg(err instanceof Error ? err.message : 'Delete failed.')
    } finally {
      setDeleting(false)
    }
  }

  const editorRole = selected === 'new' ? null : selected

  return (
    <div className="flex flex-col lg:flex-row gap-5">
      {/* Role list */}
      <div className="flex flex-col gap-3 lg:w-72 flex-shrink-0">
        <button
          type="button"
          onClick={() => setSelected('new')}
          className="flex items-center gap-2 justify-center rounded-xl px-4 py-2.5 text-sm font-semibold border-2 border-dashed transition-colors"
          style={{
            borderColor: 'rgba(30,58,95,0.25)',
            color:        NAVY,
            background:   selected === 'new' ? 'rgba(30,58,95,0.05)' : 'transparent',
          }}
          onMouseEnter={e => { if (selected !== 'new') (e.currentTarget as HTMLElement).style.background = 'rgba(30,58,95,0.04)' }}
          onMouseLeave={e => { if (selected !== 'new') (e.currentTarget as HTMLElement).style.background = 'transparent' }}
        >
          <Plus size={14} />
          New role
        </button>

        {roles.map(role => {
          const isActive = selected !== null && selected !== 'new' && selected.role_id === role.role_id
          return (
            <button
              key={role.role_id}
              type="button"
              onClick={() => setSelected(isActive ? null : role)}
              className="text-left rounded-xl p-4 transition-all"
              style={{
                background: isActive ? 'rgba(30,58,95,0.07)' : '#FFFFFF',
                border:     `1px solid ${isActive ? 'rgba(30,58,95,0.25)' : BORDER}`,
                boxShadow:  '0 1px 3px rgba(0,0,0,0.04)',
              }}
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold" style={{ color: TEXT1 }}>
                  {role.role_name}
                </span>
                <ChevronRight
                  size={14}
                  style={{
                    color:     TEXT3,
                    transform: isActive ? 'rotate(90deg)' : 'none',
                    transition: 'transform 0.2s',
                  }}
                />
              </div>
              {role.description && (
                <p className="text-xs mt-1 line-clamp-2" style={{ color: TEXT3 }}>
                  {role.description}
                </p>
              )}
              <div className="flex flex-wrap gap-1 mt-2">
                <span
                  className="text-xs px-2 py-0.5 rounded-full"
                  style={{ background: 'rgba(30,58,95,0.08)', color: NAVY }}
                >
                  {role.page_keys.length} page{role.page_keys.length !== 1 ? 's' : ''}
                </span>
                {role.entity_codes.length > 0 && (
                  <span
                    className="text-xs px-2 py-0.5 rounded-full"
                    style={{ background: '#FEF3C7', color: '#B45309' }}
                  >
                    {role.entity_codes.length} entit{role.entity_codes.length !== 1 ? 'ies' : 'y'}
                  </span>
                )}
                {role.entity_codes.length === 0 && (
                  <span
                    className="text-xs px-2 py-0.5 rounded-full"
                    style={{ background: '#DCFCE7', color: '#166534' }}
                  >
                    All entities
                  </span>
                )}
              </div>
            </button>
          )
        })}

        {/* Local status message */}
        <AnimatePresence>
          {localMsg && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="rounded-lg px-3 py-2 text-xs text-center"
              style={{ background: '#F0FDF4', color: '#166534', border: '1px solid #BBF7D0' }}
            >
              {localMsg}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Role editor */}
      <AnimatePresence>
        {selected !== null && (
          <motion.div
            key={selected === 'new' ? 'new' : (selected as AdminRole).role_id}
            initial={{ opacity: 0, x: 16 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 16 }}
            transition={{ duration: 0.22 }}
            className="flex-1 min-w-0"
            style={{ minHeight: 480 }}
          >
            <RoleEditor
              role={editorRole}
              pages={pages}
              entities={entities}
              onSave={handleSave}
              onDelete={selected !== 'new' ? handleDelete : undefined}
              onClose={() => setSelected(null)}
              saving={saving}
              deleting={deleting}
              isPreview={isPreview}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {selected === null && (
        <div
          className="flex-1 hidden lg:flex items-center justify-center rounded-2xl text-sm"
          style={{ border: `1.5px dashed ${BORDER}`, color: TEXT3, minHeight: 320 }}
        >
          Select a role to edit or create a new one
        </div>
      )}
    </div>
  )
}

// ─── Users tab ─────────────────────────────────────────────────────────────────

function UserRow({
  user,
  roles,
  onSave,
  isPreview,
}: {
  user:      AdminUser
  roles:     AdminRole[]
  onSave:    (id: number, data: { role_ids: number[]; is_admin: boolean; is_active: boolean }) => Promise<void>
  isPreview: boolean
}) {
  const [roleIds,  setRoleIds]  = useState<number[]>(user.role_ids)
  const [isAdmin,  setIsAdmin]  = useState(user.is_admin)
  const [isActive, setIsActive] = useState(user.is_active)
  const [saving,   setSaving]   = useState(false)
  const [dirty,    setDirty]    = useState(false)

  function markDirty() { setDirty(true) }

  function toggleRole(id: number, checked: boolean) {
    setRoleIds(prev => checked ? [...prev, id] : prev.filter(r => r !== id))
    markDirty()
  }

  async function handleSave() {
    setSaving(true)
    try {
      await onSave(user.user_id, { role_ids: roleIds, is_admin: isAdmin, is_active: isActive })
      setDirty(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
      {/* Email + name */}
      <td className="px-5 py-3 align-top">
        <div className="text-sm font-medium" style={{ color: TEXT1 }}>{user.display_name}</div>
        <div className="text-xs mt-0.5" style={{ color: TEXT3 }}>{user.email}</div>
      </td>

      {/* Admin badge */}
      <td className="px-5 py-3 align-middle">
        <div className="flex items-center gap-2">
          <Toggle
            checked={isAdmin}
            onChange={v => { setIsAdmin(v); markDirty() }}
            disabled={isPreview}
          />
          {isAdmin && (
            <span
              className="text-xs rounded px-1.5 py-0.5 font-medium"
              style={{ background: '#FEF3C7', color: '#B45309' }}
            >
              Admin
            </span>
          )}
        </div>
        {isAdmin && (
          <p className="text-xs mt-1" style={{ color: TEXT3 }}>Bypasses all restrictions</p>
        )}
      </td>

      {/* Active */}
      <td className="px-5 py-3 align-middle">
        <Toggle
          checked={isActive}
          onChange={v => { setIsActive(v); markDirty() }}
          disabled={isPreview}
        />
      </td>

      {/* Roles */}
      <td className="px-5 py-3 align-middle">
        {roles.length === 0 ? (
          <span className="text-xs" style={{ color: TEXT3 }}>No roles defined</span>
        ) : (
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {roles.map(r => (
              <label key={r.role_id} className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={roleIds.includes(r.role_id)}
                  onChange={e => toggleRole(r.role_id, e.target.checked)}
                  disabled={isPreview}
                  className="accent-[#1E3A5F]"
                />
                <span className="text-xs" style={{ color: TEXT2 }}>{r.role_name}</span>
              </label>
            ))}
          </div>
        )}
      </td>

      {/* Save */}
      <td className="px-5 py-3 align-middle">
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving || isPreview}
          className="flex items-center gap-1 text-xs font-semibold rounded-lg px-3 py-1.5 text-white transition-opacity"
          style={{
            background: NAVY,
            opacity:    (!dirty || saving || isPreview) ? 0.35 : 1,
            cursor:     (!dirty || saving || isPreview) ? 'not-allowed' : 'pointer',
          }}
        >
          {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
          Save
        </button>
      </td>
    </tr>
  )
}

function UsersTab({
  users,
  roles,
  isPreview,
  onRefresh,
}: {
  users:     AdminUser[]
  roles:     AdminRole[]
  isPreview: boolean
  onRefresh: () => void
}) {
  async function handleSaveUser(
    id: number,
    data: { role_ids: number[]; is_admin: boolean; is_active: boolean },
  ) {
    await api.adminUpdateUser(id, data)
    onRefresh()
  }

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{ background: '#FFFFFF', border: `1px solid ${BORDER}`, boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      {/* Info line */}
      <div
        className="px-5 py-3 text-xs flex items-center gap-2"
        style={{ background: '#EEF3FA', borderBottom: `1px solid ${BORDER}`, color: NAVY }}
      >
        <ShieldCheck size={13} />
        Admins bypass page/entity restrictions and always see everything.
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
              {['User', 'Admin', 'Active', 'Roles', ''].map(h => (
                <th
                  key={h}
                  className="px-5 py-3 text-xs font-semibold uppercase tracking-wider"
                  style={{ color: TEXT3 }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <UserRow
                key={u.user_id}
                user={u}
                roles={roles}
                onSave={handleSaveUser}
                isPreview={isPreview}
              />
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center text-sm" style={{ color: TEXT3 }}>
                  No users found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Main page ─────────────────────────────────────────────────────────────────

export default function RoleManagementPage() {
  const { user } = useAuth()

  const [activeTab,  setActiveTab]  = useState<Tab>('roles')
  const [isPreview,  setIsPreview]  = useState(false)
  const [loadError,  setLoadError]  = useState<string | null>(null)

  const [pages,    setPages]    = useState<AdminPage[]>([])
  const [roles,    setRoles]    = useState<AdminRole[]>([])
  const [users,    setUsers]    = useState<AdminUser[]>([])
  const [entities, setEntities] = useState<Entity[]>([])
  const [loading,  setLoading]  = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)

    // Entities are always attempted (existing endpoint)
    let liveEntities: Entity[] = []
    try {
      liveEntities = await api.entities()
    } catch {
      // entities not available — use empty list
    }

    // Admin endpoints — attempt all, fall back gracefully on failure
    let livePages: AdminPage[] = []
    let liveRoles: AdminRole[] = []
    let liveUsers: AdminUser[] = []
    let preview = false

    try {
      const [pagesRes, rolesRes, usersRes] = await Promise.all([
        api.adminPages(),
        api.adminRoles(),
        api.adminUsers(),
      ])
      livePages = pagesRes.pages
      liveRoles = rolesRes.roles
      liveUsers = usersRes.users
    } catch (err) {
      preview = true
      const msg = err instanceof Error ? err.message : 'Unknown error'
      setLoadError(msg)
      livePages = FALLBACK_PAGES
      liveRoles = FALLBACK_ROLES
      // Sample user = the logged-in user (if available)
      liveUsers = user
        ? [{ user_id: user.user_id, email: user.email, display_name: user.display_name, is_admin: user.is_admin, is_active: true, role_ids: [1] }]
        : []
    }

    setIsPreview(preview)
    setPages(livePages)
    setRoles(liveRoles)
    setUsers(liveUsers)
    setEntities(liveEntities.length > 0 ? liveEntities : [
      { legal_entity_code: 'DE01', entity_name: 'Muster GmbH (DE)' },
      { legal_entity_code: 'AT01', entity_name: 'Muster Austria GmbH' },
      { legal_entity_code: 'CH01', entity_name: 'Muster Schweiz AG' },
    ])
    setLoading(false)
  }, [user])

  useEffect(() => { void load() }, [load])

  return (
    <PageShell
      loading={loading}
      message="Loading role management…"
      submessage="Roles, users, and page permissions are loading."
    >
    <div className="min-h-screen" style={{ background: SURF }}>
      <div className="max-w-[1680px] mx-auto px-6 lg:px-8 py-8">

        {/* Page header — aligned with the other pages (eyebrow · h1 · subtitle) */}
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
          className="mb-6"
        >
          <p
            className="text-xs font-semibold uppercase tracking-widest mb-1.5"
            style={{ color: NAVY }}
          >
            Administration
          </p>
          <h1
            className="text-2xl font-bold tracking-tight"
            style={{ color: TEXT1 }}
          >
            Role management
          </h1>
          <p className="text-sm mt-1" style={{ color: TEXT2 }}>
            Control which pages each role can open and which entities' data they can see.
          </p>
        </motion.div>

        {/* Graceful degradation banner */}
        {isPreview && <PreviewBanner />}

        {/* Sub-tab bar */}
        <div className="mb-6">
          <SubTabBar active={activeTab} onChange={setActiveTab} />
        </div>

        {/* Content */}
        <AnimatePresence mode="wait">
          {activeTab === 'roles' ? (
            <motion.div
              key="roles"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <RolesTab
                roles={roles}
                pages={pages}
                entities={entities}
                isPreview={isPreview}
                onRefresh={load}
              />
            </motion.div>
          ) : (
            <motion.div
              key="users"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <UsersTab
                users={users}
                roles={roles}
                isPreview={isPreview}
                onRefresh={load}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Debug info (dev only) */}
        {loadError && import.meta.env.DEV && (
          <p className="mt-4 text-xs" style={{ color: TEXT3 }}>
            Backend error: {loadError}
          </p>
        )}
      </div>
    </div>
    </PageShell>
  )
}
