/**
 * ProtectedRoute — redirects to /login if no valid auth token is present.
 */

import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import PageLoadingOverlay from "./ui/PageLoadingOverlay";

interface ProtectedRouteProps {
  children: React.ReactNode;
  /** If true, additionally require is_admin — shows 403 UI for authenticated non-admins. */
  adminOnly?: boolean;
}

export default function ProtectedRoute({ children, adminOnly }: ProtectedRouteProps) {
  const { user, token, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <PageLoadingOverlay
        visible
        message="Loading…"
        submessage="Checking session and permissions."
      />
    );
  }

  if (!token || !user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (adminOnly && !user.is_admin) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="rounded-xl border border-red-200 bg-red-50 p-8 text-center max-w-sm">
          <h2 className="text-lg font-semibold text-red-800 mb-2">Access denied</h2>
          <p className="text-sm text-red-600">
            This page is only available to administrators.
          </p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
