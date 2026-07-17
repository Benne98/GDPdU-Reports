import { useCallback, useState } from 'react'
import type { FilterRule } from './rowFilterTypes'
import type { AdaptiveCardPayload } from './useFddBot'

type CardSubmit = (
  cardName: string,
  values: Record<string, unknown>,
  meta?: { submitLabel?: string; skipUserBubble?: boolean },
) => void | Promise<void>

export function useRowFilterEditor(
  payload: AdaptiveCardPayload,
  onSubmit?: CardSubmit,
) {
  const [open, setOpen] = useState(false)
  const context = String(payload.filter_context ?? '').trim()
  const headers = Array.isArray(payload.filter_headers)
    ? payload.filter_headers.map(value => String(value))
    : Array.isArray(payload.headers)
      ? payload.headers.map(value => String(value))
      : []
  const rulesJson = String(payload.filter_rules_json ?? '[]')

  const saveRules = useCallback(
    async (rules: FilterRule[]) => {
      if (!onSubmit || !context) return
      await onSubmit(
        'filter_rules_save',
        {
          filter_context: context,
          filter_rules_json: JSON.stringify(rules),
        },
        { skipUserBubble: true },
      )
    },
    [context, onSubmit],
  )

  return {
    open,
    setOpen,
    context,
    headers,
    rulesJson,
    saveRules,
    canEdit: Boolean(context && onSubmit && headers.length > 0),
  }
}
