import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type AnomalyTreeNode, type AnomalyBookingRow } from '../../lib/api'
import SignalScoreBadge from './SignalScoreBadge'

function BookingsPanel({ accountNumberGroup }: { accountNumberGroup: string }) {
  const [bookings, setBookings] = useState<AnomalyBookingRow[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.financialsAnomalyBookings(accountNumberGroup)
      .then(d => { if (!cancelled) { setBookings(d.bookings); setLoading(false) } })
      .catch((e: unknown) => { if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setLoading(false) } })
    return () => { cancelled = true }
  }, [accountNumberGroup])

  if (loading) return (
    <div className="mt-4 rounded-xl bg-white p-4" style={{ border: '1px solid #E2E8F0' }}>
      <p className="text-sm" style={{ color: '#94A3B8' }}>Loading bookings...</p>
    </div>
  )
  if (error) return (
    <div className="mt-4 rounded-xl px-4 py-3 text-sm" style={{ background: 'rgba(239,68,68,0.08)', color: '#991B1B', border: '1px solid rgba(220,38,38,0.3)' }}>{error}</div>
  )
  if (!bookings || bookings.length === 0) return (
    <div className="mt-4 rounded-xl bg-white px-4 py-3 text-sm" style={{ border: '1px solid #E2E8F0', color: '#94A3B8' }}>No bookings found.</div>
  )

  return (
    <div className="mt-4 overflow-x-auto rounded-xl bg-white" style={{ border: '1px solid #E2E8F0' }}>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ borderBottom: '1px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Date</th>
            <th className="px-3 py-2 text-right font-semibold" style={{ color: '#64748B' }}>Amount (kEUR)</th>
            <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Journal #</th>
            <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Entity</th>
            <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Note</th>
          </tr>
        </thead>
        <tbody>
          {bookings.map(b => (
            <tr
              key={b.booking_line_id}
              style={{ borderBottom: '1px solid #F1F5F9', background: b.large_booking ? 'rgba(217,119,6,0.05)' : undefined }}
            >
              <td className="px-3 py-1.5" style={{ color: '#374151' }}>{b.posting_date ?? '—'}</td>
              <td className="px-3 py-1.5 text-right font-mono" style={{ color: b.amount_keur < 0 ? '#DC2626' : '#374151' }}>
                {b.amount_keur.toFixed(1)}
              </td>
              <td className="px-3 py-1.5" style={{ color: '#374151' }}>{b.journal_entry_number}</td>
              <td className="px-3 py-1.5" style={{ color: '#374151' }}>{b.entity_prefix}</td>
              <td className="px-3 py-1.5 max-w-xs truncate" style={{ color: '#64748B' }}>{b.line_note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function findNodeByKey(nodes: AnomalyTreeNode[], key: string): AnomalyTreeNode | null {
  for (const n of nodes) {
    if (n.key === key) return n
    if (n.children.length > 0) {
      const found = findNodeByKey(n.children, key)
      if (found) return found
    }
  }
  return null
}

interface DrillTreeProps {
  nodes: AnomalyTreeNode[]
  renderChart: (node: AnomalyTreeNode) => React.ReactNode
  analysisType: 'outliers' | 'seasonality'
}

export default function DrillTree({ nodes, renderChart, analysisType: _analysisType }: DrillTreeProps) {
  const [searchParams] = useSearchParams()
  const [path, setPath] = useState<AnomalyTreeNode[]>([])
  const [selected, setSelected] = useState<AnomalyTreeNode | null>(null)
  const [showBookings, setShowBookings] = useState(false)

  useEffect(() => {
    const nodeKey = searchParams.get('node')
    if (nodeKey && nodes.length > 0) {
      const found = findNodeByKey(nodes, nodeKey)
      if (found) { setSelected(found); setPath([found]) }
    }
  }, [nodes, searchParams])

  const currentNodes = path.length === 0 ? nodes : (path[path.length - 1]?.children ?? [])
  const sorted = [...currentNodes].sort((a, b) => (b.signal_score ?? b.max_abs_z) - (a.signal_score ?? a.max_abs_z))

  function drillInto(node: AnomalyTreeNode) {
    setPath(prev => [...prev, node])
    setSelected(node)
    setShowBookings(false)
  }

  function navBreadcrumb(index: number) {
    if (index < 0) {
      setPath([])
      setSelected(null)
      setShowBookings(false)
    } else {
      const np = path.slice(0, index + 1)
      setPath(np)
      setSelected(np[np.length - 1] ?? null)
      setShowBookings(false)
    }
  }

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-1 text-xs mb-4 flex-wrap">
        <button
          type="button"
          className="font-medium hover:underline"
          style={{ color: path.length === 0 ? '#1E3A5F' : '#2563EB' }}
          onClick={() => navBreadcrumb(-1)}
        >
          All positions
        </button>
        {path.map((node, i) => (
          <span key={node.key} className="flex items-center gap-1">
            <span style={{ color: '#CBD5E1' }}>›</span>
            <button
              type="button"
              className="font-medium hover:underline"
              style={{ color: i === path.length - 1 ? '#1E3A5F' : '#2563EB' }}
              onClick={() => navBreadcrumb(i)}
            >
              {node.label}
            </button>
          </span>
        ))}
      </div>

      {/* Node list */}
      <div className="rounded-xl bg-white overflow-hidden mb-4" style={{ border: '1px solid #E2E8F0' }}>
        {sorted.length === 0 ? (
          <p className="px-5 py-6 text-sm text-center" style={{ color: '#94A3B8' }}>No positions at this level.</p>
        ) : sorted.map(node => (
          <div
            key={node.key}
            className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-50 transition-colors"
            style={{
              borderBottom: '1px solid #F1F5F9',
              background: selected?.key === node.key ? 'rgba(30,58,95,0.04)' : undefined,
            }}
            onClick={() => setSelected(node)}
          >
            <SignalScoreBadge score={node.signal_score ?? 0} band={node.band ?? 'low'} size="sm" />
            <span className="flex-1 text-sm font-medium" style={{ color: '#1E3A5F' }}>{node.label}</span>
            {node.children.length > 0 && (
              <button
                type="button"
                onClick={e => { e.stopPropagation(); drillInto(node) }}
                className="rounded px-2 py-1 text-xs font-medium transition-colors"
                style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
              >
                Dive deeper
              </button>
            )}
            {node.level === 'account' && node.children.length === 0 && (
              <button
                type="button"
                onClick={e => { e.stopPropagation(); setSelected(node); setShowBookings(true) }}
                className="rounded px-2 py-1 text-xs font-medium transition-colors"
                style={{ background: 'rgba(37,99,235,0.08)', color: '#2563EB' }}
              >
                Show bookings
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Chart for selected node */}
      {selected && !showBookings && (
        <div className="mb-4">{renderChart(selected)}</div>
      )}

      {/* Bookings panel */}
      {selected && showBookings && selected.account_number_group && (
        <BookingsPanel accountNumberGroup={selected.account_number_group} />
      )}
    </div>
  )
}
