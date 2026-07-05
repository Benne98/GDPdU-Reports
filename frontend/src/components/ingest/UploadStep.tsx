/**
 * UploadStep — Step 0 of the Data Update wizard.
 *
 * Fully props-driven: receives only an onUploaded callback.
 * Internal state (dragging, loading, error, pending multi-sheet selection) is
 * self-contained and does NOT depend on IngestionPage internals.
 *
 * Extracted from IngestionPage.tsx so it can be mounted in any parent wizard.
 */

import { useRef, useState } from "react";
import { uploadFile, type UploadResponse } from "../../lib/gdpduApi";
import { StepCard } from "./IngestStepCard";

// ---------------------------------------------------------------------------
// Helper — detect BS/PL Master workbook (two canonical sheets)
// ---------------------------------------------------------------------------

export function isBsPlMasterSheets(sheets: string[]): boolean {
  return sheets.includes("Master_BS") && sheets.includes("Master_PL");
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface UploadStepProps {
  /** Called when the file has been uploaded (and a sheet selected if needed). */
  onUploaded: (result: UploadResponse, file: File) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function UploadStep({ onUploaded }: UploadStepProps) {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSheet, setSelectedSheet] = useState<string | undefined>();
  const [pendingResult, setPendingResult] = useState<{
    res: UploadResponse;
    file: File;
  } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function doUpload(file: File, sheet?: string) {
    setLoading(true);
    setError(null);
    try {
      const res = await uploadFile(file, sheet);
      if (res.sheets.length > 1 && !sheet && !isBsPlMasterSheets(res.sheets)) {
        setPendingResult({ res, file });
        setLoading(false);
        return;
      }
      onUploaded(res, file);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setLoading(false);
    }
  }

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    doUpload(files[0]);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  }

  function handleSheetConfirm() {
    if (!pendingResult) return;
    doUpload(pendingResult.file, selectedSheet || pendingResult.res.sheets[0]);
    setPendingResult(null);
  }

  if (pendingResult) {
    return (
      <StepCard
        title="Select worksheet"
        subtitle="This file has multiple sheets — choose which one to import."
      >
        <div className="space-y-3">
          {pendingResult.res.sheets.map((s) => (
            <label key={s} className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                name="sheet"
                value={s}
                checked={(selectedSheet ?? pendingResult.res.sheets[0]) === s}
                onChange={() => setSelectedSheet(s)}
                className="h-4 w-4 accent-blue-600"
              />
              <span className="text-sm font-medium text-slate-700">{s}</span>
            </label>
          ))}
        </div>
        <div className="mt-5 flex gap-3">
          <button
            type="button"
            onClick={() => setPendingResult(null)}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSheetConfirm}
            disabled={loading}
            className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            {loading ? "Loading..." : "Confirm"}
          </button>
        </div>
      </StepCard>
    );
  }

  return (
    <StepCard
      title="Upload file"
      subtitle="CSV, TXT, XLSX or Parquet — encoding and delimiter are detected automatically."
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-14 cursor-pointer transition ${
          dragging
            ? "border-blue-500 bg-blue-50"
            : "border-slate-300 bg-slate-50 hover:border-blue-400 hover:bg-slate-100"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.txt,.xlsx,.xls,.parquet,text/plain,text/csv"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <div className="text-4xl text-slate-300 mb-3">&#128196;</div>
        {loading ? (
          <p className="text-sm font-medium text-blue-600">Processing file…</p>
        ) : (
          <>
            <p className="text-sm font-semibold text-slate-700">
              Drop a file here or click to browse
            </p>
            <p className="mt-1 text-xs text-slate-400">CSV · TXT · XLSX · Parquet</p>
          </>
        )}
      </div>
      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}
    </StepCard>
  );
}
