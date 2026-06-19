import PlDetailOverlay from '../../pl-two-view/PlDetailOverlay'
import type { PlNarrativeBullet } from '../../pl-two-view/plNarrativeEngine'
import type { PlIntroFacts } from '../../../../lib/api'

type Props = {
  bullet: PlNarrativeBullet
  year: number
  month: number
  entity?: string
  narrativeContext?: {
    headline?: string
    intro?: string
    intro_facts?: PlIntroFacts
  }
  onClose: () => void
}

/** Working capital bullet detail — uses WC line-detail API via PlDetailOverlay statement prop. */
export default function WcDetailOverlay(props: Props) {
  return <PlDetailOverlay {...props} statement="wc" />
}
