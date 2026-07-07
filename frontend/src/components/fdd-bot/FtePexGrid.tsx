/** Manual PEX (GL personnel expenses) entry grid — EURk per FY × entity. */

import { useMemo, useState } from 'react'
import type { AdaptiveCardInput } from './useFddBot'

interface Props {
  input: AdaptiveCardInput
  values: Record<string, unknown>
  disabled?: boolean
  onChange: (values: Record<string, number | string>) => void
}

export default function FtePexGrid({ input, values, disabled, onChange }: Props) {
  const fyOptions = input.options ?? []
  const entityCount = Math.max(1, input.entity_count ?? 1)
  const defaultNames = input.default_entity_names ?? []

  const [entityNames, setEntityNames] = useState<string[]>(() =>
    Array.from({ length: entityCount }, (_, i) => defaultNames[i] ?? `Entity ${i + 1}`),
  )

  const gridValues = useMemo(() => {
    const raw = values[input.id]
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      return raw as Record<string, number | string>
    }
    return {}
  }, [values, input.id])

  const cellKey = (entityIdx: number, fy: string) => `entity${entityIdx + 1}_${fy}`

  const setCell = (entityIdx: number, fy: string, val: string) => {
    const key = cellKey(entityIdx, fy)
    const next = { ...gridValues, [key]: val === '' ? '' : Number(val) }
    onChange(next)
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr>
            <th className="border px-2 py-1 text-left bg-slate-50">Entity</th>
            {fyOptions.map(o => (
              <th key={o.value} className="border px-2 py-1 bg-slate-50">
                {o.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: entityCount }, (_, ei) => (
            <tr key={ei}>
              <td className="border px-1 py-1">
                <input
                  className="w-full text-xs border rounded px-1"
                  value={entityNames[ei] ?? ''}
                  disabled={disabled || entityCount === 1}
                  onChange={e => {
                    const next = [...entityNames]
                    next[ei] = e.target.value
                    setEntityNames(next)
                  }}
                />
              </td>
              {fyOptions.map(o => {
                const key = cellKey(ei, o.value)
                const v = gridValues[key]
                return (
                  <td key={o.value} className="border px-1 py-1">
                    <input
                      type="number"
                      step="0.1"
                      className="w-full text-xs border rounded px-1 text-right"
                      placeholder="EURk"
                      disabled={disabled}
                      value={v === undefined || v === '' ? '' : String(v)}
                      onChange={e => setCell(ei, o.value, e.target.value)}
                    />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[12px] text-slate-500 mt-1">Values in EURk (thousands). Leave blank where unknown.</p>
    </div>
  )
}
