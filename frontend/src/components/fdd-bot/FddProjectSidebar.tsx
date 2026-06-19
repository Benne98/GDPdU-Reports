import { Plus, X } from 'lucide-react'
import type { FddProjectSummary } from './fddProjectStore'

export type FddProjectSidebarProps = {
  projects: FddProjectSummary[]
  activeProjectId: string
  onNewProject: () => void
  onSelectProject: (id: string) => void
  onDeleteProject: (id: string) => void
}

export default function FddProjectSidebar({
  projects,
  activeProjectId,
  onNewProject,
  onSelectProject,
  onDeleteProject,
}: FddProjectSidebarProps) {
  return (
    <aside
      className="flex flex-col shrink-0 h-full"
      style={{
        width: 260,
        background: '#FFFFFF',
        borderRight: '1px solid #E2E8F0',
      }}
    >
      <div className="p-3">
        <button
          type="button"
          onClick={onNewProject}
          className="flex items-center gap-2 w-full px-3 py-2.5 rounded-lg text-sm font-medium transition-colors"
          style={{
            color: '#1E3A5F',
            border: '1px solid #E2E8F0',
            background: '#F8FAFC',
          }}
        >
          <Plus size={16} />
          Neues Projekt
        </button>
      </div>

      <div className="px-4 pb-2">
        <p className="text-xs font-bold uppercase tracking-wide" style={{ color: '#64748B' }}>
          Aktuelle Projekte
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {projects.length === 0 ? (
          <p className="px-2 text-xs" style={{ color: '#94A3B8' }}>
            Noch keine Projekte gespeichert.
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {projects.map(project => {
              const isActive = project.id === activeProjectId
              return (
                <li key={project.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelectProject(project.id)}
                    className="w-full text-left px-3 py-2 pr-9 rounded-lg text-sm truncate transition-colors"
                    style={{
                      background: isActive ? 'rgba(30,58,95,0.08)' : 'transparent',
                      color: isActive ? '#1E3A5F' : '#334155',
                      fontWeight: isActive ? 600 : 400,
                    }}
                    title={project.name}
                  >
                    {project.name}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteProject(project.id)
                    }}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ color: '#94A3B8' }}
                    aria-label="Projekt löschen"
                  >
                    <X size={14} />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </aside>
  )
}
