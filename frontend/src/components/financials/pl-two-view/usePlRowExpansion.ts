import { useCallback, useMemo, useState } from 'react'
import type { FinancialStatementRow } from '../../../lib/api'
import { computeAutoExpandedIds } from '../statementRowExpansion'

/**
 * Row expand/collapse (XOR with user toggles) — matches FinancialStatementTable defaults per statement.
 */
export function usePlRowExpansion(
  rows: FinancialStatementRow[] | undefined,
  statement?: string,
) {
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())

  const autoExpandedIds = useMemo(
    () => computeAutoExpandedIds(rows, statement),
    [rows, statement],
  )

  const checkOpen = useCallback(
    (id: string) => autoExpandedIds.has(id) !== userToggles.has(id),
    [autoExpandedIds, userToggles],
  )

  const toggle = useCallback((id: string) => {
    setUserToggles(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  return { checkOpen, toggle }
}
