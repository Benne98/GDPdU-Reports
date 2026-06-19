export type AppUserProfile = {
  display_name: string
  email: string
  default_language: 'de' | 'en'
}

const KEY = 'finssentials.app.userProfile.v1'

// The user is already authenticated to open the app (AuthContext stores the
// account under this key). Action Notes must NOT ask for a separate sign-in /
// profile — derive the identity from the logged-in account instead.
const AUTH_USER_KEY = 'gdpdu_user'

function profileFromAuthUser(): AppUserProfile | null {
  try {
    const raw = localStorage.getItem(AUTH_USER_KEY)
    if (!raw) return null
    const u = JSON.parse(raw) as { email?: string; display_name?: string }
    if (!u || (!u.email && !u.display_name)) return null
    return {
      display_name: (u.display_name || u.email || '').trim(),
      email: (u.email || '').trim(),
      default_language: 'de',
    }
  } catch {
    return null
  }
}

export function loadUserProfile(): AppUserProfile | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as AppUserProfile
  } catch {
    // ignore malformed override and fall through to the authenticated account
  }
  // No explicit override saved → use the logged-in account (no second sign-in).
  return profileFromAuthUser()
}

export function saveUserProfile(p: AppUserProfile): void {
  localStorage.setItem(KEY, JSON.stringify(p))
}

export function authorScope(): string {
  const p = loadUserProfile()
  if (p?.email) return `user:${p.email}`
  let dev = localStorage.getItem('finssentials.app.deviceId.v1')
  if (!dev) {
    dev = `device_${crypto.randomUUID().slice(0, 12)}`
    localStorage.setItem('finssentials.app.deviceId.v1', dev)
  }
  return `device:${dev}`
}
