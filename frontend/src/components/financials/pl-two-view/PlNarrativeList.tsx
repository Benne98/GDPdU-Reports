import { Link } from 'react-router-dom'
import PlCommentIndexBadge from './PlCommentIndexBadge'
import { bulletDisplayText, type PlNarrativeBullet } from './plNarrativeEngine'
import PlNarrativeRichText from './PlNarrativeRichText'
import { capitalizeSentenceStarts } from '../../../lib/capitalizeSentenceStarts'

type Props = {
  intro?: string | null
  bullets: PlNarrativeBullet[]
  loading?: boolean
  onSelect: (bullet: PlNarrativeBullet) => void
}

export default function PlNarrativeList({ intro, bullets, loading, onSelect }: Props) {
  if (loading && !bullets.length && !intro) {
    return (
      <p className="text-xs leading-relaxed" style={{ color: '#64748B' }}>
        Loading narrative…
      </p>
    )
  }

  if (!bullets.length && !intro) {
    return (
      <p className="text-xs leading-relaxed" style={{ color: '#64748B' }}>
        No analytical comments for this period — check postings and filters.
      </p>
    )
  }

  return (
    <div>
      {intro && (
        <p
          className="text-xs leading-relaxed mb-4"
          style={{ color: '#475569', textAlign: 'justify' }}
        >
          {capitalizeSentenceStarts(intro)}
        </p>
      )}
      {bullets.length > 0 && (
        <ol className="list-none m-0 p-0 space-y-4">
          {bullets.map(b => (
            <li key={b.line_code}>
              <button
                type="button"
                onClick={() => onSelect(b)}
                className="flex w-full gap-2.5 text-left rounded-sm px-0 py-0 transition-colors hover:opacity-90 group"
              >
                <span className="flex h-[1.625rem] shrink-0 items-start pt-0.5">
                  <PlCommentIndexBadge index={b.index} />
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className="block text-xs leading-relaxed normal-case"
                    style={{ color: '#475569', textAlign: 'justify', textTransform: 'none' }}
                  >
                    <PlNarrativeRichText text={capitalizeSentenceStarts(bulletDisplayText(b))} />
                  </span>
                  {b.deep_links && b.deep_links.length > 0 && (
                    <span className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1">
                      {b.deep_links.map(link => (
                        <Link
                          key={`${b.line_code}-${link.route}`}
                          to={link.route}
                          onClick={e => e.stopPropagation()}
                          className="text-[0.65rem] leading-snug underline hover:opacity-80"
                          style={{ color: '#0E7490' }}
                        >
                          {link.label}
                        </Link>
                      ))}
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
