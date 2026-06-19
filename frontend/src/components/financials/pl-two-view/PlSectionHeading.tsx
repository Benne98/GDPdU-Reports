import { plSectionHeadingStyle } from './plReportSectionHeadings'

type Props = {
  children: string
  className?: string
}

export default function PlSectionHeading({ children, className = '' }: Props) {
  return (
    <p className={`mb-2 leading-snug ${className}`.trim()} style={plSectionHeadingStyle}>
      {children}
    </p>
  )
}
