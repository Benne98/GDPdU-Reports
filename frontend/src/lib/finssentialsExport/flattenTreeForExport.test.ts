/**
 * Unit checks for export tree flattening (run: npx tsx src/lib/finssentialsExport/flattenTreeForExport.test.ts)
 */
import assert from 'node:assert/strict'
import { flattenTreeForExport } from './flattenTreeForExport'

type Node = {
  id: string
  label: string
  row_kind: string
  children?: Node[]
}

const tree: Node[] = [
  {
    id: 'parent',
    label: 'Parent',
    row_kind: 'subtotal',
    children: [
      { id: 'child-a', label: 'Child A', row_kind: 'line' },
      {
        id: 'child-b',
        label: 'Child B',
        row_kind: 'line',
        children: [{ id: 'grand', label: 'Grand', row_kind: 'line' }],
      },
    ],
  },
]

function run() {
  const allOpen = flattenTreeForExport(tree, {
    isRowOpen: () => true,
    mapRow: row => ({ label: row.label, values: [], kind: 'data' }),
  })
  assert.equal(allOpen.length, 4, 'all nodes when expanded')

  const collapsed = flattenTreeForExport(tree, {
    isRowOpen: id => id === 'parent',
    mapRow: row => ({ label: row.label, values: [], kind: 'data' }),
  })
  assert.equal(collapsed.length, 4, 'children still emitted for Excel outline')
  const hiddenKids = collapsed.filter(r => r.id !== 'parent' && r.hidden)
  assert.ok(hiddenKids.length >= 2, 'collapsed branch rows are hidden')

  const order = flattenTreeForExport(tree, {
    isRowOpen: () => true,
    mapRow: row => ({ label: row.label, values: [], kind: 'data' }),
  }).map(r => r.id)
  assert.deepEqual(order, ['parent', 'child-a', 'child-b', 'grand'], 'preorder table order')

  console.log('flattenTreeForExport: all checks passed')
}

run()
