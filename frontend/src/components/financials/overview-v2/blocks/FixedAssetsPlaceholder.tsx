/**
 * FixedAssetsPlaceholder — Block (3): Fixed assets.
 * PERMANENT placeholder — Area 3 is out of scope until fact_fixed_asset is loaded
 * AND transfer-sign + depreciation-method are approved (docs/financial-logic.md F1/F2).
 * See docs/overview-v2-redesign-plan.md §7 (P6) and §2 Area 3 iron-rule stop.
 */
import OverviewAnalysisBlock from '../OverviewAnalysisBlock'
import { Lock } from 'lucide-react'

export default function FixedAssetsPlaceholder() {
  return (
    <OverviewAnalysisBlock title="Fixed Assets" deepLink="/balance-sheet">
      <div
        className="flex flex-col items-center justify-center gap-2 rounded-lg py-10 text-center"
        style={{ background: '#F8FAFC', border: '1px dashed #CBD5E1' }}
      >
        <Lock size={18} aria-hidden style={{ color: '#94A3B8' }} />
        <p className="text-xs font-semibold" style={{ color: '#64748B' }}>
          Fixed Assets register not yet loaded
        </p>
        <p className="text-[10px] max-w-[260px] leading-relaxed" style={{ color: '#94A3B8' }}>
          Module unavailable until fact_fixed_asset is loaded and transfer-sign +
          depreciation-method are confirmed (P6 — requires F1/F2 sign-off).
        </p>
      </div>
    </OverviewAnalysisBlock>
  )
}
