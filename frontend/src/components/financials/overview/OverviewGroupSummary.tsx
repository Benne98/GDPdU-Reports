import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import { capitalizeSentenceStarts } from '../../../lib/capitalizeSentenceStarts'

type Props = {
  intro: string
}

export default function OverviewGroupSummary({ intro }: Props) {
  return (
    <div>
      <PlSectionHeading>Group overview</PlSectionHeading>
      <p
        className="text-xs leading-relaxed m-0"
        style={{ color: '#475569', textAlign: 'justify' }}
      >
        {capitalizeSentenceStarts(intro)}
      </p>
    </div>
  )
}
