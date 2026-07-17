/**
 * FddChatPanel — FDD Bot with project sidebar (fixed below app chrome).
 */

import { createPortal } from 'react-dom'
import { useState } from 'react'
import { ListFilter, Undo2 } from 'lucide-react'
import type { FddBotApi } from './useFddBot'
import BotConversation from './BotConversation'
import FddProjectSidebar from './FddProjectSidebar'
import RowFilterModal from './RowFilterModal'
import type { FilterRule } from './rowFilterTypes'

const DEFAULT_TOP_OFFSET_PX = 64

export type FddPanelMode = 'closed' | 'expanded'

export type FddChatPanelProps = {
  mode: FddPanelMode
  preloadedFile?: File | null
  bot: FddBotApi
  topOffsetPx?: number
  bottomOffsetPx?: number
}

function FddChatPanelView({
  mode,
  preloadedFile,
  bot,
  topOffsetPx = DEFAULT_TOP_OFFSET_PX,
  bottomOffsetPx = 0,
}: FddChatPanelProps) {
  const [salesFilterOpen, setSalesFilterOpen] = useState(false)
  const [salesFilterRulesJson, setSalesFilterRulesJson] = useState('[]')
  const [salesFilterHeaders, setSalesFilterHeaders] = useState<string[]>([])
  const {
    loading,
    scriptJobLoading,
    undoLastCard,
    canUndo,
    createProject,
    switchProject,
    deleteProject,
    activeProjectId,
    projectName,
    projects,
    salesFilterContext,
    submitCard,
    readTrackerSlots,
    latestBotCard,
  } = bot
  const busy = loading || scriptJobLoading

  if (mode === 'closed') {
    return null
  }

  const openSalesFilter = async () => {
    if (!salesFilterContext) return
    const slots = await readTrackerSlots()
    const rulesRaw = slots[`${salesFilterContext}_filter_rules_json`]
    const headersRaw = slots.headers ?? latestBotCard?.filter_headers ?? latestBotCard?.headers
    let headers: string[] = []
    if (Array.isArray(headersRaw)) {
      headers = headersRaw.map(value => String(value))
    } else if (typeof headersRaw === 'string') {
      try {
        const parsed = JSON.parse(headersRaw)
        if (Array.isArray(parsed)) headers = parsed.map(value => String(value))
      } catch {
        headers = []
      }
    }
    setSalesFilterRulesJson(typeof rulesRaw === 'string' ? rulesRaw : JSON.stringify(rulesRaw ?? []))
    setSalesFilterHeaders(headers)
    setSalesFilterOpen(true)
  }

  const saveSalesFilter = async (rules: FilterRule[]) => {
    if (!salesFilterContext) return
    await submitCard(
      'filter_rules_save',
      {
        filter_context: salesFilterContext,
        filter_rules_json: JSON.stringify(rules),
      },
      { skipUserBubble: true },
    )
  }

  const headerActions = (
    <div className="flex items-center gap-1.5">
      {salesFilterContext ? (
        <button
          type="button"
          onClick={() => void openSalesFilter()}
          title="Edit sales row filters for the current revenue analysis"
          className="mr-1 inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50"
          style={{ background: 'rgba(255,255,255,0.14)', color: '#FFFFFF' }}
        >
          <ListFilter size={14} />
          Sales row filter
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => void undoLastCard()}
        disabled={!canUndo || busy}
        title="Letzte Karte zurück"
        className="p-1.5 rounded-lg transition-colors disabled:opacity-40"
        style={{ color: 'rgba(255,255,255,0.85)' }}
        aria-label="Letzte Karte zurück"
      >
        <Undo2 size={16} />
      </button>
    </div>
  )

  const panel = (
    <div
      className="fixed left-0 right-0 z-40 flex"
      style={{
        top: topOffsetPx,
        bottom: bottomOffsetPx,
        background: '#F8FAFC',
      }}
    >
      <FddProjectSidebar
        projects={projects}
        activeProjectId={activeProjectId}
        onNewProject={createProject}
        onSelectProject={switchProject}
        onDeleteProject={deleteProject}
      />

      <div className="flex flex-col flex-1 min-w-0 min-h-0">
        <div
          className="flex items-center justify-between px-5 py-3 shrink-0"
          style={{
            background: '#1E3A5F',
            boxShadow: '0 2px 8px rgba(30,58,95,0.25)',
          }}
        >
          <div className="flex items-center gap-2.5 min-w-0">
            <div
              className="flex items-center justify-center rounded-full text-xs font-bold shrink-0"
              style={{ width: 30, height: 30, background: 'rgba(255,255,255,0.15)', color: '#fff' }}
            >
              FDD
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold truncate" style={{ color: '#FFFFFF' }}>
                {projectName}
              </p>
              <p className="text-xs" style={{ color: 'rgba(255,255,255,0.6)' }}>
                {busy ? 'Thinking…' : 'Ready'}
              </p>
            </div>
          </div>
          {headerActions}
        </div>

        <div className="flex flex-1 flex-col min-h-0 min-w-0">
          <BotConversation
            bot={bot}
            active={mode === 'expanded'}
            autoHello
            preloadedFile={preloadedFile}
            wide
          />
        </div>
      </div>

      <RowFilterModal
        open={salesFilterOpen}
        title="Sales row filter"
        headers={salesFilterHeaders}
        rulesJson={salesFilterRulesJson}
        onClose={() => setSalesFilterOpen(false)}
        onSave={saveSalesFilter}
      />
    </div>
  )

  return createPortal(panel, document.body)
}

export default function FddChatPanel(props: FddChatPanelProps) {
  return <FddChatPanelView {...props} />
}
