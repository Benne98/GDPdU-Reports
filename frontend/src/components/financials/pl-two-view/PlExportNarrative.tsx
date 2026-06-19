import PlCommentIndexBadge from './PlCommentIndexBadge'
import { bulletDisplayText, type PlNarrativeBullet } from './plNarrativeEngine'

type Props = {
  intro?: string | null
  bullets: PlNarrativeBullet[]
}

/** Static narrative block for PDF / print capture (no click handlers). */
export default function PlExportNarrative({ intro, bullets }: Props) {
  return (
    <div>
      {intro && (
        <p style={{ fontSize: 12, lineHeight: 1.5, color: '#475569', margin: '0 0 16px' }}>
          {intro}
        </p>
      )}
      {bullets.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, paddingLeft: 16 }}>
          {bullets.map(b => (
            <li
              key={b.line_code}
              style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 12 }}
            >
              <span style={{ display: 'flex', height: 26, flexShrink: 0, alignItems: 'center' }}>
                <PlCommentIndexBadge index={b.index} />
              </span>
              <span
                style={{
                  fontSize: 12,
                  lineHeight: 1.5,
                  color: '#475569',
                  flex: 1,
                  minWidth: 0,
                  textTransform: 'none',
                }}
              >
                {bulletDisplayText(b)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
