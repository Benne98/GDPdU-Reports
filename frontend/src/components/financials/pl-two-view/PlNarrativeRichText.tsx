/** Renders narrative prose with *italic* segments (account names). */

type Props = {
  text: string
  className?: string
  style?: React.CSSProperties
}

export default function PlNarrativeRichText({ text, className, style }: Props) {
  const parts = text.split(/(\*[^*]+\*)/g)
  return (
    <span className={className} style={style}>
      {parts.map((part, i) => {
        if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
          return (
            <em key={i} style={{ fontStyle: 'italic' }}>
              {part.slice(1, -1)}
            </em>
          )
        }
        return <span key={i}>{part}</span>
      })}
    </span>
  )
}
