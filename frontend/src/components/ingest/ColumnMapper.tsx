/**
 * ColumnMapper — two-panel native HTML5 drag-and-drop column mapper.
 *
 * Left panel: source columns as draggable chips with 1-2 sample values.
 * Right panel: target field drop-zones grouped into Entry / Line / Partner.
 *
 * Required fields are highlighted in amber until mapped.
 * Mapped fields show the assigned source column and a clear button.
 */

import { useState } from "react";
import { IS_DATA_UPDATE_V4 } from "../../lib/dataUpdateMode";

// ---------------------------------------------------------------------------
// Target field manifest
// ---------------------------------------------------------------------------

export type FieldGroup = string;

export interface TargetField {
  key: string;
  label: string;
  group: FieldGroup;
  required: boolean;
  hint?: string;
}

export const TARGET_FIELDS: TargetField[] = [
  // Entry
  { key: "journal_entry_number", label: "Journal Entry Number", group: "Entry", required: true, hint: "GoBD: Transaction number — groups all lines of one booking" },
  { key: "posting_date", label: "Posting Date", group: "Entry", required: true, hint: "Booking document date — fiscal year is derived from this" },
  { key: "document_date", label: "Document Date", group: "Entry", required: false, hint: "Document date" },
  { key: "document_type", label: "Document Type", group: "Entry", required: false },
  { key: "reference_document_number", label: "Reference / document number", group: "Entry", required: false },
  { key: "currency", label: "Currency", group: "Entry", required: false, hint: "Default: EUR" },
  { key: "header_note", label: "Header Note", group: "Entry", required: false },
  // Line
  { key: "account_number", label: "Account Number", group: "Line", required: true },
  { key: "amount", label: "Amount", group: "Line", required: true, hint: "Signed, or use sign-mode options" },
  { key: "vat_amount", label: "VAT Amount", group: "Line", required: false },
  { key: "line_note", label: "Line Note", group: "Line", required: false },
  { key: "posting_type", label: "Posting Type", group: "Line", required: false },
  // Partner
  { key: "source_type", label: "Source Type", group: "Partner", required: false, hint: "Debtor / creditor" },
  { key: "source_no", label: "Source No.", group: "Partner", required: false, hint: "Debtor/creditor number" },
];

/**
 * Returns the GL target field manifest for the current mode.
 * v4 (port 5178): omits document_type + reference_document_number,
 *                 relabels journal_entry_number → 'Booking ID'.
 * non-v4: returns TARGET_FIELDS unchanged (byte-identical reference).
 * Also serves glGroupTargetFields (change 2).
 */
export function glTargetFields(): TargetField[] {
  if (!IS_DATA_UPDATE_V4) return TARGET_FIELDS;
  return TARGET_FIELDS
    .filter(f => f.key !== 'document_type' && f.key !== 'reference_document_number')
    .map(f => f.key === 'journal_entry_number' ? { ...f, label: 'Booking ID' } : f);
}

/**
 * GL group-upload target field manifest: Entity field prepended to glTargetFields().
 * Used by GlGroupUploadCard (change 1, 2).
 */
export function glGroupTargetFields(): TargetField[] {
  return [
    { key: 'entity', label: 'Entity', group: 'Entry', required: true, hint: 'Column identifying the entity (name or prefix) per row' },
    ...glTargetFields(),
  ];
}

// Groups are derived dynamically from the active field manifest (see orderedGroups in component).

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ColumnMapperProps {
  /** Source columns from the uploaded file */
  sourceColumns: string[];
  /** Sample rows for preview tooltip/chip subtitle */
  sample: Record<string, unknown>[];
  /** Current mapping: targetField -> sourceColumn */
  mapping: Record<string, string>;
  onChange: (mapping: Record<string, string>) => void;
  /** Optional custom field manifest. Defaults to TARGET_FIELDS (GL fields).
   *  Groups are rendered in first-seen order from the manifest. */
  fields?: TargetField[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getSampleValues(col: string, sample: Record<string, unknown>[]): string {
  const vals = sample
    .map((row) => row[col])
    .filter((v) => v !== null && v !== undefined && v !== "")
    .slice(0, 2)
    .map(String);
  return vals.join(" · ");
}

// Group-filtering is done inline inside the component against the active field manifest.

// ---------------------------------------------------------------------------
// Drag-chip component (source column)
// ---------------------------------------------------------------------------

interface ChipProps {
  col: string;
  sample: Record<string, unknown>[];
  isMapped: boolean;
}

function SourceChip({ col, sample, isMapped }: ChipProps) {
  const preview = getSampleValues(col, sample);

  function onDragStart(e: React.DragEvent<HTMLDivElement>) {
    e.dataTransfer.setData("text/plain", col);
    e.dataTransfer.effectAllowed = "move";
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className={`cursor-grab select-none rounded-md border px-2.5 py-1.5 text-sm leading-tight transition ${
        isMapped
          ? "border-slate-200 bg-slate-100 text-slate-400"
          : "border-blue-200 bg-blue-50 text-blue-800 hover:bg-blue-100 hover:border-blue-300 active:cursor-grabbing"
      }`}
      title={preview ? `Samples: ${preview}` : col}
    >
      <span className="font-medium">{col}</span>
      {preview && (
        <span className="ml-1.5 text-xs text-slate-500 truncate max-w-[120px] inline-block align-bottom">
          {preview}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Drop-zone component (target field)
// ---------------------------------------------------------------------------

interface DropZoneProps {
  field: TargetField;
  mappedCol: string | undefined;
  onDrop: (targetKey: string, sourceCol: string) => void;
  onClear: (targetKey: string) => void;
}

function DropZone({ field, mappedCol, onDrop, onClear }: DropZoneProps) {
  const [over, setOver] = useState(false);

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setOver(true);
  }

  function handleDragLeave() {
    setOver(false);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
    const col = e.dataTransfer.getData("text/plain");
    if (col) onDrop(field.key, col);
  }

  const isMapped = Boolean(mappedCol);
  const isRequired = field.required;

  let borderClass = "border-slate-200 bg-white";
  if (over) borderClass = "border-blue-400 bg-blue-50";
  else if (isMapped) borderClass = "border-emerald-300 bg-emerald-50";
  else if (isRequired) borderClass = "border-amber-300 bg-amber-50";

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`flex min-h-[44px] items-center gap-2 rounded-md border px-3 py-1.5 transition ${borderClass}`}
    >
      {/* Label */}
      <div className="min-w-0 flex-1">
        <span className="text-sm font-medium text-slate-700">{field.label}</span>
        {isRequired && !isMapped && (
          <span className="ml-1.5 text-xs font-semibold text-amber-600">*</span>
        )}
        {field.hint && (
          <span className="ml-2 text-xs text-slate-400">{field.hint}</span>
        )}
      </div>

      {/* Mapped column badge */}
      {isMapped ? (
        <div className="flex items-center gap-1 rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
          <span>{mappedCol}</span>
          <button
            type="button"
            onClick={() => onClear(field.key)}
            className="ml-0.5 rounded hover:bg-emerald-200 p-0.5 leading-none text-emerald-700"
            aria-label={`Clear mapping for ${field.label}`}
          >
            &#x2715;
          </button>
        </div>
      ) : (
        <span className="text-xs text-slate-400">Drop here</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ColumnMapper component
// ---------------------------------------------------------------------------

export default function ColumnMapper({
  sourceColumns,
  sample,
  mapping,
  onChange,
  fields = glTargetFields(),
}: ColumnMapperProps) {
  // Derive ordered unique group list from the active manifest (preserves first-seen order).
  // For the default TARGET_FIELDS this produces exactly: Entry, Line, Partner.
  const orderedGroups = fields.reduce<string[]>((acc, f) => {
    if (!acc.includes(f.group)) acc.push(f.group);
    return acc;
  }, []);

  // Track which source columns are already mapped (may appear in multiple targets, so just detect usage)
  const mappedSources = new Set(Object.values(mapping));

  function handleDrop(targetKey: string, sourceCol: string) {
    onChange({ ...mapping, [targetKey]: sourceCol });
  }

  function handleClear(targetKey: string) {
    const next = { ...mapping };
    delete next[targetKey];
    onChange(next);
  }

  return (
    <div className="flex gap-6 h-full">
      {/* Left panel — source columns */}
      <div className="w-64 shrink-0">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-700">Source columns</h3>
          <span className="text-xs text-slate-400">
            {sourceColumns.length - mappedSources.size} unmapped
          </span>
        </div>
        <div className="max-h-[520px] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2">
          {sourceColumns.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">No columns detected</p>
          ) : (
            sourceColumns.map((col) => (
              <SourceChip
                key={col}
                col={col}
                sample={sample}
                isMapped={mappedSources.has(col)}
              />
            ))
          )}
        </div>
        <p className="mt-2 text-xs text-slate-400 leading-relaxed">
          Drag a column chip onto a target field. Mapped chips are greyed out but can be reused.
        </p>
      </div>

      {/* Right panel — target field drop-zones grouped */}
      <div className="flex-1 min-w-0">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-700">Target fields</h3>
          <span className="text-xs text-slate-400">
            <span className="text-amber-600 font-medium">*</span> = required
          </span>
        </div>
        <div className="max-h-[520px] overflow-y-auto space-y-5 pr-1">
          {orderedGroups.map((group) => (
            <div key={group}>
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  {group}
                </span>
                <div className="flex-1 border-t border-slate-200" />
              </div>
              <div className="space-y-2">
                {fields.filter((f) => f.group === group).map((field) => (
                  <DropZone
                    key={field.key}
                    field={field}
                    mappedCol={mapping[field.key]}
                    onDrop={handleDrop}
                    onClear={handleClear}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Utility: check required fields
// ---------------------------------------------------------------------------

export function missingRequiredFields(
  mapping: Record<string, string>,
  fields: TargetField[] = TARGET_FIELDS,
): string[] {
  return fields.filter((f) => f.required && !mapping[f.key]).map((f) => f.label);
}

// ---------------------------------------------------------------------------
// Required GL column-mapping keys (single source of truth)
//
// These are the profile.columns keys that the backend's apply_profile requires.
// Note: amount/sign source columns live in profile.sign (OptionsStep), NOT here.
// ---------------------------------------------------------------------------

export const REQUIRED_GL_COLUMN_KEYS = [
  'posting_date',
  'journal_entry_number',
  'account_number',
] as const

export type RequiredGlColumnKey = typeof REQUIRED_GL_COLUMN_KEYS[number]

/** User-facing labels for required GL column fields — reused in UI messages. */
export const GL_COLUMN_FRIENDLY_LABELS: Record<RequiredGlColumnKey, string> = {
  posting_date: 'Posting date',
  journal_entry_number: 'Transaction/journal number',
  account_number: 'Account number',
}

/**
 * Returns the RequiredGlColumnKey values that are absent from the provided
 * profile.columns mapping.  Empty array means all required fields are present.
 */
export function missingRequiredGlColumns(
  mapping: Record<string, string>,
): RequiredGlColumnKey[] {
  return REQUIRED_GL_COLUMN_KEYS.filter(k => !mapping[k])
}
