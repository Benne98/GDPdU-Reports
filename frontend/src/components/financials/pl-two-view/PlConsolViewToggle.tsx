import { LayoutList, Table2, Users } from 'lucide-react'
import { PL_TOOLBAR_BTN, PL_TOOLBAR_BTN_STYLE } from './plToolbarButton'

export type PlConsolViewMode = 'report' | 'table' | 'group'

type Props = {
  mode: PlConsolViewMode
  onChange: (mode: PlConsolViewMode) => void
  disabled?: boolean
}

const MODES: Array<{ id: PlConsolViewMode; label: string; icon: typeof Table2 }> = [
  { id: 'table', label: 'Table view', icon: Table2 },
  { id: 'report', label: 'Entity report', icon: LayoutList },
  { id: 'group', label: 'Group report', icon: Users },
]

export default function PlConsolViewToggle({ mode, onChange, disabled }: Props) {
  return (
    <div
      className="flex items-center rounded-md overflow-hidden"
      style={{ border: '1px solid #E2E8F0', background: '#F8FAFC' }}
    >
      {MODES.map(({ id, label, icon: Icon }, idx) => {
        const active = mode === id
        return (
          <button
            key={id}
            type="button"
            disabled={disabled}
            onClick={() => onChange(id)}
            title={label}
            className={`${PL_TOOLBAR_BTN} px-2 gap-1`}
            style={{
              ...PL_TOOLBAR_BTN_STYLE,
              cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.5 : 1,
              background: active ? 'rgba(30,58,95,0.1)' : 'transparent',
              color: active ? '#1E3A5F' : '#64748B',
              fontWeight: active ? 600 : 400,
              borderRight: idx < MODES.length - 1 ? '1px solid #E2E8F0' : undefined,
            }}
          >
            <Icon size={12} strokeWidth={1.75} />
            <span className="hidden sm:inline">{label}</span>
          </button>
        )
      })}
    </div>
  )
}
