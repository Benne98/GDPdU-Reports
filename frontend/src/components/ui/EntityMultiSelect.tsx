import { useEffect, useRef, useState } from 'react'
import { Building2, Check, ChevronDown } from 'lucide-react'

import type { Entity } from '../../lib/api'
import { stripLegalForm } from '../../lib/stripLegalForm'

type Props = {
  entities: Entity[]
  /** Selected legal_entity_codes. Empty array = ALL entities (consolidated view). */
  selected: string[]
  onChange: (codes: string[]) => void
}

/**
 * Compact multi-select entity filter (Phase 7). Shown only on the entity-scoped
 * sub-pages (Profitability / Payroll / Fixed assets / OPOS aging). Empty selection
 * means "all entities" — matching the pre-Phase-7 consolidated default.
 */
export default function EntityMultiSelect({ entities, selected, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  // Matches the legacy single-select dropdown, which listed every entity as-is.
  const options = entities

  useEffect(() => {
    if (!open) return
    function onDocClick(ev: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(ev.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const selectedSet = new Set(selected)
  const allSelected = selected.length === 0

  function toggle(code: string) {
    const next = new Set(selectedSet)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    onChange([...next])
  }

  const summary = allSelected
    ? 'All entities'
    : selected.length === 1
      ? stripLegalForm(options.find(e => e.legal_entity_code === selected[0])?.entity_name ?? selected[0])
      : `${selected.length} entities`

  return (
    <div className="relative" ref={rootRef}>
      <div className="flex items-center gap-2 flex-wrap">
        <Building2 size={13} className="shrink-0" style={{ color: '#94A3B8' }} />
        <span className="text-xs font-medium shrink-0" style={{ color: '#475569' }}>
          Entities
        </span>
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium outline-none cursor-pointer"
          style={{ background: '#F4F6F9', border: '1px solid #CBD5E1', color: '#111827' }}
          aria-expanded={open}
          aria-haspopup="listbox"
        >
          <span className="truncate max-w-[12rem]">{summary}</span>
          <ChevronDown
            size={13}
            style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}
            aria-hidden
          />
        </button>
      </div>

      {open && (
        <div
          className="absolute left-0 top-full mt-1.5 z-50 rounded-xl p-1.5 max-h-72 overflow-auto"
          style={{ minWidth: 236, background: '#FFFFFF', border: '1px solid #E8EDF3', boxShadow: '0 16px 40px rgba(15,30,50,0.16)' }}
          role="listbox"
          aria-multiselectable
        >
          <button
            type="button"
            onClick={() => onChange([])}
            className="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-sm text-left"
            style={{
              color: allSelected ? '#1E3A5F' : '#475569',
              fontWeight: allSelected ? 600 : 500,
              background: allSelected ? 'rgba(30,58,95,0.08)' : 'transparent',
            }}
          >
            <span>All entities</span>
            {allSelected && <Check size={15} strokeWidth={2.5} style={{ color: '#1E3A5F', flexShrink: 0 }} aria-hidden />}
          </button>
          <div className="my-1 h-px" style={{ background: '#F1F5F9' }} />
          {options.map(e => {
            const active = selectedSet.has(e.legal_entity_code)
            return (
              <button
                key={e.legal_entity_code}
                type="button"
                onClick={() => toggle(e.legal_entity_code)}
                className="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-sm text-left"
                style={{
                  color: active ? '#1E3A5F' : '#475569',
                  fontWeight: active ? 600 : 500,
                  background: active ? 'rgba(30,58,95,0.05)' : 'transparent',
                }}
                role="option"
                aria-selected={active}
              >
                <span className="truncate">{stripLegalForm(e.entity_name)}</span>
                {active && <Check size={15} strokeWidth={2.5} style={{ color: '#1E3A5F', flexShrink: 0 }} aria-hidden />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
