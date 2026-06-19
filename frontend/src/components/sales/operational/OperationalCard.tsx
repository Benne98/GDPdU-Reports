export default function OperationalCard({
  title,
  subtitle,
  headerRight,
  children,
  className = '',
}: {
  title: string
  subtitle?: string
  headerRight?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={`rounded-xl flex flex-col ${className}`}
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
    >
      <div className="px-5 pt-4 pb-2 border-b flex items-start justify-between gap-3" style={{ borderColor: '#F1F5F9' }}>
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>{title}</h3>
          {subtitle && <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>{subtitle}</p>}
        </div>
        {headerRight}
      </div>
      <div className="p-4 flex-1 min-h-0 flex flex-col min-h-[120px]">{children}</div>
    </div>
  )
}
