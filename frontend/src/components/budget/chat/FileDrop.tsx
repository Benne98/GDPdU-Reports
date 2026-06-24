/**
 * FileDrop — drag-and-drop area + Browse button for file uploads.
 * Accepts .xlsx,.xls by default. Shows spinner while loading.
 */

import { useRef, useState } from 'react';

interface FileDropProps {
  onFile: (f: File) => void;
  accept?: string;
  loading?: boolean;
  label?: string;
  hint?: string;
}

export default function FileDrop({
  onFile,
  accept = '.xlsx,.xls',
  loading,
  label = 'Drop your Excel file here',
  hint = 'or click Browse to select',
}: FileDropProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) onFile(file);
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onFile(file);
    // Reset so the same file can be re-selected
    e.target.value = '';
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={[
        'rounded-xl border-2 border-dashed px-6 py-8 flex flex-col items-center gap-3 transition-colors',
        dragging
          ? 'border-[#1E3A5F] bg-[#1E3A5F]/5'
          : 'border-[#1E3A5F]/30 bg-white hover:border-[#1E3A5F]/50 hover:bg-slate-50/50',
        loading ? 'pointer-events-none opacity-60' : 'cursor-pointer',
      ].join(' ')}
      onClick={() => !loading && inputRef.current?.click()}
    >
      {loading ? (
        <svg
          className="animate-spin h-8 w-8 text-[#1E3A5F]"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          aria-label="Loading"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8v8H4z"
          />
        </svg>
      ) : (
        <svg
          className="h-8 w-8 text-[#1E3A5F]/50"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
          />
        </svg>
      )}

      <div className="text-center">
        <p className="text-sm font-medium text-slate-700">{label}</p>
        <p className="text-xs text-slate-400 mt-0.5">{hint}</p>
      </div>

      {!loading && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}
          className="rounded-lg border border-[#1E3A5F] px-4 py-1.5 text-xs font-medium text-[#1E3A5F] hover:bg-[#1E3A5F] hover:text-white transition-colors"
        >
          Browse
        </button>
      )}

      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={handleChange}
      />
    </div>
  );
}
