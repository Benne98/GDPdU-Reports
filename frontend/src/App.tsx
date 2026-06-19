import { Navigate, Route, Routes } from "react-router-dom";
import GdpduFooter from "./components/GdpduFooter";
import AppHeader from "./components/AppHeader";
import { AuthProvider } from "./context/AuthContext";
import { ActionNotesProvider } from "./components/action-notes/ActionNotesContext";
import ProtectedRoute from "./components/ProtectedRoute";
import BalanceSheetPage from "./pages/BalanceSheetPage";
import CashFlowPage from "./pages/CashFlowPage";
import HomePage from "./pages/HomePage";
import OverviewPage from "./pages/OverviewPage";
import IncomeStatementPage from "./pages/IncomeStatementPage";
import IngestionPage from "./pages/IngestionPage";
import MappingEditorPage from "./pages/MappingEditorPage";
import LoginPage from "./pages/LoginPage";
import FddBotPage from "./pages/FddBotPage";
import PlanPage from "./pages/PlanPage";
import WorkingCapitalPage from "./pages/WorkingCapitalPage";
import RoleManagementPage from "./pages/RoleManagementPage";
import AccountStatementPage from "./pages/AccountStatementPage";

// ---------------------------------------------------------------------------
// Shell — uses AppHeader + GdpduFooter
// ---------------------------------------------------------------------------

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-full flex flex-col">
      <AppHeader />
      <main className="w-full flex-1">{children}</main>
      <GdpduFooter />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// AppRoutes
// ---------------------------------------------------------------------------

function AppRoutes() {
  return (
    <Routes>
      {/* Public — login */}
      <Route path="/login" element={<LoginPage />} />

      {/* Home — module hub */}
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Shell>
              <HomePage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* ── Reporting routes ── */}

      {/* Overview — real implementation */}
      <Route
        path="/overview"
        element={
          <ProtectedRoute>
            <Shell>
              <OverviewPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* Income statement — real implementation */}
      <Route
        path="/income-statement"
        element={
          <ProtectedRoute>
            <Shell>
              <IncomeStatementPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* Balance sheet — real implementation */}
      <Route
        path="/balance-sheet"
        element={
          <ProtectedRoute>
            <Shell>
              <BalanceSheetPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* Working capital — real implementation */}
      <Route
        path="/working-capital"
        element={
          <ProtectedRoute>
            <Shell>
              <WorkingCapitalPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* Cash flow — real implementation (includes Cash & debt sub-tab) */}
      <Route
        path="/cash-flow"
        element={
          <ProtectedRoute>
            <Shell>
              <CashFlowPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* /cash-debt redirects to /cash-flow — Cash & debt is now a sub-tab there */}
      <Route path="/cash-debt" element={<Navigate to="/cash-flow" replace />} />

      {/* Account statement */}
      <Route
        path="/account-statement"
        element={
          <ProtectedRoute>
            <Shell>
              <AccountStatementPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* ── Non-reporting routes (accessible via hub) ── */}

      {/* FDD-Bot — full page */}
      <Route
        path="/fdd-bot"
        element={
          <ProtectedRoute>
            <Shell>
              <FddBotPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* ── Admin-only routes ── */}

      <Route
        path="/ingestion"
        element={
          <ProtectedRoute adminOnly>
            <Shell>
              <div className="mx-auto w-full max-w-[1680px] px-6 py-8">
                <IngestionPage />
              </div>
            </Shell>
          </ProtectedRoute>
        }
      />

      <Route
        path="/mapping-editor"
        element={
          <ProtectedRoute adminOnly>
            <Shell>
              <div className="mx-auto w-full max-w-[1680px] px-6 py-8">
                <MappingEditorPage />
              </div>
            </Shell>
          </ProtectedRoute>
        }
      />

      <Route
        path="/plan"
        element={
          <ProtectedRoute adminOnly>
            <Shell>
              <div className="mx-auto w-full max-w-[1680px] px-6 py-8">
                <PlanPage />
              </div>
            </Shell>
          </ProtectedRoute>
        }
      />

      <Route
        path="/role-management"
        element={
          <ProtectedRoute adminOnly>
            <Shell>
              <RoleManagementPage />
            </Shell>
          </ProtectedRoute>
        }
      />

      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

// ---------------------------------------------------------------------------
// App — routes wrapped in AuthProvider
// ---------------------------------------------------------------------------

export default function App() {
  return (
    <AuthProvider>
      <ActionNotesProvider
        initialFilters={{
          route: "/",
          year: 2025,
          month: 1,
          entity: "all",
        }}
      >
        <AppRoutes />
      </ActionNotesProvider>
    </AuthProvider>
  );
}
