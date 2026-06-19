/**
 * Generic narrative types for all financial statements.
 * P&L uses the same shape today; BS/WC/CF will extend intro_facts over time.
 */
export type {
  PlNarrativeTone as StatementNarrativeTone,
  PlNarrativeDeepLink as StatementNarrativeDeepLink,
  PlNarrativeBullet as StatementNarrativeBullet,
  PlIntroFacts as StatementIntroFacts,
  PlNarrativeResponse as StatementNarrativeResponse,
  PlLineDetailResponse as StatementLineDetailResponse,
} from '../../../lib/api'

/** UI bullet after mapping API rows to statement tree */
export type {
  PlNarrativeBullet as StatementNarrativeUiBullet,
  PlNarrativeTone as StatementNarrativeUiTone,
  PlDeepLink as StatementDeepLink,
} from '../pl-two-view/plNarrativeEngine'
