/**
 * Unit checks for report comment marker elevation (run: npx tsx src/components/financials/statement-two-view/reportCommentMarkers.test.ts)
 */
import assert from 'node:assert/strict'
import type { FinancialStatementRow } from '../../../lib/api'
import { computeAutoExpandedIds } from '../statementRowExpansion'
import {
  buildReportCommentMarkerMap,
  collectVisibleLineCodes,
  resolveMarkerAnchorLineCode,
  resolveVisibleMarkerLineCode,
} from './reportCommentMarkers'

function bsCheckOpen(rows: FinancialStatementRow[]) {
  const auto = computeAutoExpandedIds(rows, 'bs')
  return (id: string) => auto.has(id)
}

/** Depth-3 line under depth-2 parent — child hidden with BS default expansion (maxDepth 2). */
const provisionsTree: FinancialStatementRow[] = [
  {
    id: 'bs-root',
    line_code: 'bs-root',
    label: 'Liabilities',
    row_kind: 'subtotal',
    children: [
      {
        id: 'bs-liab',
        line_code: 'bs-liab',
        label: 'Current liabilities',
        row_kind: 'subtotal',
        children: [
          {
            id: 'bs-other-prov',
            line_code: 'bs-other-prov',
            label: 'Other provisions & accruals',
            row_kind: 'line',
            children: [
              {
                id: 'bs-fin-prov',
                line_code: 'bs-fin-prov',
                label: 'Financial accounting provisions',
                row_kind: 'line',
              },
            ],
          },
        ],
      },
    ],
  },
] as FinancialStatementRow[]

const checkOpen = bsCheckOpen(provisionsTree)

assert.ok(collectVisibleLineCodes(provisionsTree, checkOpen).has('bs-other-prov'))
assert.ok(!collectVisibleLineCodes(provisionsTree, checkOpen).has('bs-fin-prov'))

assert.equal(
  resolveVisibleMarkerLineCode('bs-fin-prov', provisionsTree, checkOpen),
  'bs-other-prov',
)
assert.equal(
  resolveMarkerAnchorLineCode('bs-fin-prov', provisionsTree, checkOpen),
  'bs-other-prov',
)

const collapsedMap = buildReportCommentMarkerMap(
  [{ index: 5, line_code: 'bs-fin-prov' }],
  provisionsTree,
  checkOpen,
)
assert.deepEqual(collapsedMap['bs-other-prov'], { tier: 'primary', index: 5 })
assert.equal(collapsedMap['bs-fin-prov'], undefined)

const openAll = () => true
const expandedMap = buildReportCommentMarkerMap(
  [{ index: 5, line_code: 'bs-fin-prov' }],
  provisionsTree,
  openAll,
)
assert.deepEqual(expandedMap['bs-other-prov'], { tier: 'primary', index: 5 })
assert.deepEqual(expandedMap['bs-fin-prov'], { tier: 'sub', index: 5, sub: 1 })

const twoChildMap = buildReportCommentMarkerMap(
  [
    { index: 5, line_code: 'bs-fin-prov' },
    { index: 3, line_code: 'bs-other-prov' },
  ],
  provisionsTree,
  openAll,
)
assert.deepEqual(twoChildMap['bs-other-prov'], { tier: 'primary', index: 3 })
assert.deepEqual(twoChildMap['bs-fin-prov'], { tier: 'sub', index: 3, sub: 1 }) // primary index = min(3, 5)

const visibleOnlyMap = buildReportCommentMarkerMap(
  [{ index: 2, line_code: 'bs-other-prov' }],
  provisionsTree,
  checkOpen,
)
assert.deepEqual(visibleOnlyMap['bs-other-prov'], { tier: 'primary', index: 2 })
assert.equal(visibleOnlyMap['bs-fin-prov'], undefined)

console.log('reportCommentMarkers.test.ts: all assertions passed')
