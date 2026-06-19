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

/** Balance sheet bullet detail — uses BS line-detail API via PlDetailOverlay statement prop. */
export default function BsDetailOverlay(props: Props) {
  return <PlDetailOverlay {...props} statement="bs" />
}
