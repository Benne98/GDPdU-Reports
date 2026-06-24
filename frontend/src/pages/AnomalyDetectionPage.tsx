/**
 * Anomaly Detection page — Phase 6 frontend (reworked).
 * Takes a `view` prop, renders the matching view component.
 * No CollapsibleModuleFiltersCard, no entity/period selectors, no boot logic.
 */
import { motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import AnomalyOverview from '../components/anomaly/AnomalyOverview'
import OutliersView from '../components/anomaly/OutliersView'
import SeasonalityView from '../components/anomaly/SeasonalityView'
import ForensicView from '../components/anomaly/ForensicView'
import FloatingAssistants from '../components/action-notes/FloatingAssistants'

interface Props {
  view: 'overview' | 'outliers' | 'seasonality' | 'forensic'
}

export default function AnomalyDetectionPage({ view }: Props) {
  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="mx-auto w-full max-w-[1680px] px-6 py-8">

        {/* Page header */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-6"
        >
          <div className="text-xs font-semibold uppercase tracking-widest mb-1.5" style={{ color: '#1E3A5F' }}>
            Reporting
          </div>
          <div className="flex items-center gap-2.5">
            <AlertTriangle size={22} style={{ color: '#D97706' }} aria-hidden />
            <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
              Anomaly Detection
            </h1>
          </div>
          <p className="text-sm mt-1" style={{ color: '#94A3B8' }}>
            Material MoM/YoY swings, sign flips, balance breaks and GL concentration flags
            across P&amp;L, Balance Sheet, Working Capital and Cash Flow
          </p>
        </motion.div>

        {/* View content */}
        {view === 'overview'    && <AnomalyOverview />}
        {view === 'outliers'    && <OutliersView />}
        {view === 'seasonality' && <SeasonalityView />}
        {view === 'forensic'    && <ForensicView />}

        <div className="mt-16" />
      </div>

      <FloatingAssistants
        year={new Date().getFullYear()}
        month={new Date().getMonth() + 1}
        entity="all"
        route="/anomaly-detection"
        sessionTitle="Anomaly Detection"
      />
    </div>
  )
}
