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

export type FddProjectMode = 'upload' | 'pipeline'

export interface FddProjectSnapshot {
  messages: StoredChatMessage[]
  datesContext: StoredDatesContext | null
  uploadContext: StoredUploadContext | null
  columnRoles: Record<string, string>
  firstFy: string | null
  submittedCardIds: string[]
  undoStack: SerializedUndoFrame[]
  /** Which tile this chat was created in. Legacy records default to 'upload'. */
  mode?: FddProjectMode
}

export interface FddProjectRecord {
  id: string
  name: string
  senderId: string
  updatedAt: string
  snapshot: FddProjectSnapshot
  /** Denormalised copy of snapshot.mode for easy filtering without loading snapshot. */
  mode: FddProjectMode
}

export type FddProjectSummary = Pick<FddProjectRecord, 'id' | 'name' | 'updatedAt' | 'mode'>

function makeProjectId(): string {
  return crypto.randomUUID()
}

function makeSenderId(): string {
  return crypto.randomUUID().slice(0, 8)
}

function emptySnapshot(mode: FddProjectMode = 'upload'): FddProjectSnapshot {
  return {
    messages: [],
    datesContext: null,
    uploadContext: null,
    columnRoles: {},
    firstFy: null,
    submittedCardIds: [],
    undoStack: [],
    mode,
  }
}

export function createEmptyProject(
  name = DEFAULT_PROJECT_NAME,
  mode: FddProjectMode = 'upload',
): FddProjectRecord {
  const now = new Date().toISOString()
  return {
    id: makeProjectId(),
    name,
    senderId: makeSenderId(),
    updatedAt: now,
    mode,
    snapshot: emptySnapshot(mode),
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

/** Coerce a raw stored record so it always carries a valid `mode` field. */
function migrateRecord(record: FddProjectRecord): FddProjectRecord {
  const mode: FddProjectMode =
    record.mode === 'pipeline' ? 'pipeline' : 'upload'
  const snapshotMode: FddProjectMode =
    record.snapshot?.mode === 'pipeline' ? 'pipeline' : 'upload'
  if (record.mode === mode && record.snapshot?.mode === snapshotMode) return record
  return {
    ...record,
    mode,
    snapshot: { ...record.snapshot, mode: snapshotMode },
  }
}

export function listProjectSummaries(): FddProjectSummary[] {
  return readProjectsRaw()
    .map(migrateRecord)
    .map(({ id, name, updatedAt, mode }) => ({ id, name, updatedAt, mode }))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
}

export function listProjectSummariesByMode(mode: FddProjectMode): FddProjectSummary[] {
  return listProjectSummaries().filter(p => p.mode === mode)
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
  const next = { ...record, updatedAt: new Date().toISOString() }
  if (idx >= 0) {
    projects[idx] = next
  } else {
    projects.unshift(next)
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

/** Migrate legacy single sender_id into first project entry, and ensure all
 *  records carry a valid `mode` field (legacy records default to 'upload'). */
export function ensureMigratedProjects(): FddProjectRecord {
  let projects = readProjectsRaw()
  if (projects.length === 0) {
    const legacySender = localStorage.getItem(LEGACY_SENDER_KEY)
    const project = createEmptyProject(legacySender ? 'Projekt' : DEFAULT_PROJECT_NAME, 'upload')
    if (legacySender) {
      project.senderId = legacySender
    }
    projects = [project]
    writeProjects(projects)
    setActiveProjectId(project.id)
    return project
  }

  // Backfill mode on any existing records that predate this field.
  const migrated = projects.map(migrateRecord)
  const anyChanged = migrated.some((r, i) => r !== projects[i])
  if (anyChanged) writeProjects(migrated)
  projects = migrated

  const activeId = getActiveProjectId()
  const active = activeId ? projects.find(p => p.id === activeId) : null
  if (active) return active

  const first = projects[0]
  setActiveProjectId(first.id)
  return first
}
