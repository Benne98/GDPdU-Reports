import { Navigate, Route, Routes } from "react-router-dom";
import GdpduFooter from "./components/GdpduFooter";
import AppHeader from "./components/AppHeader";
import Sidebar from "./components/Sidebar";
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
import AnomalyDetectionPage from "./pages/AnomalyDetectionPage";
import ProjectSetupWizard from "./pages/ProjectSetupWizard";
// BudgetPage kept for reference — not routed (replaced by BudgetChatPage)
// import BudgetPage from "./pages/BudgetPage";
import BudgetChatPage from "./pages/BudgetChatPage";

// ---------------------------------------------------------------------------
// Shell — uses AppHeader + GdpduFooter
// ---------------------------------------------------------------------------

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col" style={{ background: '#F4F6F9' }}>
      <AppHeader />
      <div className="flex-1 flex min-w-0">
        <Sidebar />
        <main className="flex-1 w-full min-w-0">{children}</main>
      </div>
      {/* Footer/credentials span full width BELOW the rail — the sidebar stops before it. */}
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

      {/* Anomaly Detection */}
      <Route
        path="/anomaly-detection"
        element={
          <ProtectedRoute>
            <Shell>
              <AnomalyDetectionPage view="overview" />
            </Shell>
          </ProtectedRoute>
        }
      />
      <Route
        path="/anomaly-detection/outliers"
        element={
          <ProtectedRoute>
            <Shell>
              <AnomalyDetectionPage view="outliers" />
            </Shell>
          </ProtectedRoute>
        }
      />
      <Route
        path="/anomaly-detection/seasonality"
        element={
          <ProtectedRoute>
            <Shell>
              <AnomalyDetectionPage view="seasonality" />
            </Shell>
          </ProtectedRoute>
        }
      />
      <Route
        path="/anomaly-detection/forensic"
        element={
          <ProtectedRoute>
            <Shell>
              <AnomalyDetectionPage view="forensic" />
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
        path="/budget"
        element={
          <ProtectedRoute adminOnly>
            <Shell>
              <BudgetChatPage />
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

      <Route
        path="/project-setup"
        element={
          <ProtectedRoute adminOnly>
            <Shell>
              <ProjectSetupWizard />
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
