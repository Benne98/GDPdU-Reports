/**
 * HomePage — redirects to the first module visible to the current user.
 *
 * The tile grid, ModuleCard, and "For Finssentials staff" divider have been
 * removed in Phase B. Navigation is now driven by the Sidebar rail.
 *
 * Fallback: /overview (ProtectedRoute will handle auth; the reporting page is
 * always the safe default landing spot).
 */

import { Navigate } from 'react-router-dom'
import { useVisibleModules } from '../lib/moduleRegistry'

export default function HomePage() {
  const { all } = useVisibleModules()
  const firstPath = all[0]?.path ?? '/overview'
  return <Navigate to={firstPath} replace />
}
