import { useEffect, useRef, useState } from 'react'
import { Download, FileSpreadsheet, FileText, Presentation } from 'lucide-react'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from './plToolbarButton'

export type PlExportKind = 'pdf' | 'pptx' | 'xlsx'

type Props = {
  disabled?: boolean
  onExport: (kind: PlExportKind) => Promise<void>
  /** Defaults to pdf + xlsx */
  formats?: PlExportKind[]
}

export default function PlExportMenu({ disabled, onExport, formats = ['pdf', 'pptx', 'xlsx'] }: Props) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState<PlExportKind | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  async function run(kind: PlExportKind) {
    if (loading || disabled) return
    setLoading(kind)
    setOpen(false)
    try {
      await onExport(kind)
    } finally {
      setLoading(null)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled || loading != null}
        title="Export"
        aria-label="Export"
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
        className={PL_TOOLBAR_ICON_BTN}
        style={{
          ...PL_TOOLBAR_BTN_STYLE,
          cursor: disabled || loading ? 'not-allowed' : 'pointer',
          opacity: disabled || loading ? 0.5 : 1,
        }}
      >
        <Download size={14} strokeWidth={1.75} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-50 min-w-[220px] rounded-lg border border-slate-200 bg-white py-1 shadow-lg"
          role="menu"
        >
          {formats.includes('pdf') && (
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-start gap-2.5 px-3 py-2 text-left hover:bg-slate-50 transition-colors"
              onClick={() => void run('pdf')}
            >
              <FileText size={15} className="shrink-0 mt-0.5 text-[#1E3A5F]" strokeWidth={1.75} />
              <span>
                <span className="block text-xs font-medium text-slate-800">PDF report</span>
                <span className="block text-[0.65rem] text-slate-500 mt-0.5">One-page view with narrative &amp; table</span>
              </span>
            </button>
          )}
          {formats.includes('pptx') && (
            <button
              type="button"
              role="menuitem"
              className={`flex w-full items-start gap-2.5 px-3 py-2 text-left hover:bg-slate-50 transition-colors ${
                formats.includes('pdf') ? 'border-t border-slate-100' : ''
              }`}
              onClick={() => void run('pptx')}
            >
              <Presentation size={15} className="shrink-0 mt-0.5 text-[#1E3A5F]" strokeWidth={1.75} />
              <span>
                <span className="block text-xs font-medium text-slate-800">PowerPoint report</span>
                <span className="block text-[0.65rem] text-slate-500 mt-0.5">One slide with narrative &amp; table</span>
              </span>
            </button>
          )}
          {formats.includes('xlsx') && (
            <button
              type="button"
              role="menuitem"
              className={`flex w-full items-start gap-2.5 px-3 py-2 text-left hover:bg-slate-50 transition-colors border-t border-slate-100`}
              onClick={() => void run('xlsx')}
            >
              <FileSpreadsheet size={15} className="shrink-0 mt-0.5 text-[#1E3A5F]" strokeWidth={1.75} />
              <span>
                <span className="block text-xs font-medium text-slate-800">Excel data</span>
                <span className="block text-[0.65rem] text-slate-500 mt-0.5">Underlying figures (.xlsx)</span>
              </span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}
