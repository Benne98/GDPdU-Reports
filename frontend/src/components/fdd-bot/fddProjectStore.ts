/**
 * Client-side persistence for FDD Bot projects (chat + config snapshots).
 */

const PROJECTS_KEY = 'fdd_bot_projects_v1'
const ACTIVE_PROJECT_KEY = 'fdd_bot_active_project_id_v1'
const LEGACY_SENDER_KEY = 'fdd_bot_sender_id'

export const DEFAULT_PROJECT_NAME = 'Neues Projekt'

export interface StoredChatMessage {
  id: string
  role: 'user' | 'bot'
  text?: string
  custom?: Record<string, unknown>
  timestamp: string
}

export interface StoredUploadContext {
  file_id: string
  session_id: string
  file_path?: string
  sheet_names?: string[]
  headers?: string[]
  sheet_name?: string
}

export interface StoredDatesContext {
  ltm_month: string
  fy_end_month: string
  fy_end_day: number
  first_fy?: number
}

export interface SerializedUndoFrame {
  slots: Record<string, unknown>
  redoAction: string
  userMessageId: string
}

export interface FddProjectSnapshot {
  messages: StoredChatMessage[]
  datesContext: StoredDatesContext | null
  uploadContext: StoredUploadContext | null
  columnRoles: Record<string, string>
  firstFy: string | null
  submittedCardIds: string[]
  undoStack: SerializedUndoFrame[]
}

export interface FddProjectRecord {
  id: string
  name: string
  senderId: string
  createdAt: string
  updatedAt: string
  snapshot: FddProjectSnapshot
}

export type FddProjectSummary = Pick<FddProjectRecord, 'id' | 'name' | 'updatedAt'>

function makeProjectId(): string {
  return crypto.randomUUID()
}

function makeSenderId(): string {
  return crypto.randomUUID().slice(0, 8)
}

function emptySnapshot(): FddProjectSnapshot {
  return {
    messages: [],
    datesContext: null,
    uploadContext: null,
    columnRoles: {},
    firstFy: null,
    submittedCardIds: [],
    undoStack: [],
  }
}

export function createEmptyProject(name = DEFAULT_PROJECT_NAME): FddProjectRecord {
  const now = new Date().toISOString()
  return {
    id: makeProjectId(),
    name,
    senderId: makeSenderId(),
    createdAt: now,
    updatedAt: now,
    snapshot: emptySnapshot(),
  }
}

function readProjectsRaw(): FddProjectRecord[] {
  try {
    const raw = localStorage.getItem(PROJECTS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as FddProjectRecord[]
    if (!Array.isArray(parsed)) return []
    return parsed
  } catch {
    return []
  }
}

function writeProjects(projects: FddProjectRecord[]): void {
  localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects))
}

export function listProjectSummaries(): FddProjectSummary[] {
  return readProjectsRaw().map(({ id, name, updatedAt }) => ({ id, name, updatedAt }))
}

export function getProject(id: string): FddProjectRecord | null {
  return readProjectsRaw().find(p => p.id === id) ?? null
}

export function getActiveProjectId(): string | null {
  return localStorage.getItem(ACTIVE_PROJECT_KEY)
}

export function setActiveProjectId(id: string): void {
  localStorage.setItem(ACTIVE_PROJECT_KEY, id)
  const project = getProject(id)
  if (project) {
    localStorage.setItem(LEGACY_SENDER_KEY, project.senderId)
  }
}

export function upsertProject(record: FddProjectRecord): void {
  const projects = readProjectsRaw()
  const idx = projects.findIndex(p => p.id === record.id)
  const now = new Date().toISOString()
  const createdAt = idx >= 0 ? (projects[idx].createdAt || projects[idx].updatedAt || now) : now
  const next = { ...record, createdAt, updatedAt: now }
  if (idx >= 0) {
    projects[idx] = next
  } else {
    projects.push(next)
  }
  writeProjects(projects)
}

export function deleteProjectById(id: string): void {
  const projects = readProjectsRaw().filter(p => p.id !== id)
  writeProjects(projects)
  if (getActiveProjectId() === id) {
    localStorage.removeItem(ACTIVE_PROJECT_KEY)
  }
}

/** Migrate legacy single sender_id into first project entry. */
export function ensureMigratedProjects(): FddProjectRecord {
  let projects = readProjectsRaw()
  if (projects.length === 0) {
    const legacySender = localStorage.getItem(LEGACY_SENDER_KEY)
    const project = createEmptyProject(legacySender ? 'Projekt' : DEFAULT_PROJECT_NAME)
    if (legacySender) {
      project.senderId = legacySender
    }
    projects = [project]
    writeProjects(projects)
    setActiveProjectId(project.id)
    return project
  }

  const activeId = getActiveProjectId()
  const active = activeId ? projects.find(p => p.id === activeId) : null
  if (active) return active

  const first = projects[0]
  setActiveProjectId(first.id)
  return first
}
