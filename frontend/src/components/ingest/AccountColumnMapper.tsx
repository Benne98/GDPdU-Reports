/**
 * AccountColumnMapper — drag-and-drop column mapper for the Konten-Mapping wizard.
 *
 * Reuses the same two-panel drag-and-drop pattern as ColumnMapper but with
 * the account-dimension target fields from etl/mapping_account.py:
 *
 * Required: account_number, level_0, level_1, level_2, level_3, level_4, l4_sub
 * Optional: level_2_sort, level_3_sort, is_ic, account_name, gl_account_id,
 *           l6_na_mapping, l7_na_description,
 *           cf_l1, cf_l2, cf_l3, cf_l4, cf_l5, cf_mapping
 */

import { useState } from "react";
import { v4Label } from "../../lib/dataUpdateMode";

// ---------------------------------------------------------------------------
// Target field manifest — mirrors etl/mapping_account.py REQUIRED/OPTIONAL
// ---------------------------------------------------------------------------

export type AccountFieldGroup = "Core" | "Sort & Flags" | "NA-Mapping" | "CF-Mapping" | "Additional Information";

export interface AccountTargetField {
  key: string;
  label: string;
  group: AccountFieldGroup;
  required: boolean;
  hint?: string;
}

export const ACCOUNT_TARGET_FIELDS: AccountTargetField[] = [
  // Core — required
  { key: "account_number", label: "Account Number", group: "Core", required: true, hint: "Account number" },
  { key: "level_0", label: "Level 0 (PL/BS)", group: "Core", required: true, hint: "Statement type" },
  { key: "level_1", label: "Level 1", group: "Core", required: true },
  { key: "level_2", label: "Level 2", group: "Core", required: true },
  { key: "level_3", label: "Level 3", group: "Core", required: true },
  { key: "level_4", label: "Level 4", group: "Core", required: false },
  { key: "l4_sub", label: "L4 Sub", group: "Core", required: false },
  // Sort & Flags — optional
  { key: "level_2_sort", label: "Level 2 Sort", group: "Sort & Flags", required: false },
  { key: "level_3_sort", label: "Level 3 Sort", group: "Sort & Flags", required: false },
  { key: "is_ic", label: "Is Intercompany", group: "Sort & Flags", required: false, hint: "Boolean" },
  { key: "account_name", label: v4Label("Account Name", "Account name"), group: "Sort & Flags", required: false },
  { key: "gl_account_id", label: "GL Account ID", group: "Sort & Flags", required: false },
  // NA-Mapping — optional
  { key: "l6_na_mapping", label: "L6 NA Mapping", group: "NA-Mapping", required: false, hint: "TWC/OWC/etc." },
  { key: "l7_na_description", label: "L7 NA Description", group: "NA-Mapping", required: false },
  // CF-Mapping — optional
  { key: "cf_l1", label: "CF Level 1", group: "CF-Mapping", required: false },
  { key: "cf_l2", label: "CF Level 2", group: "CF-Mapping", required: false },
  { key: "cf_l3", label: "CF Level 3", group: "CF-Mapping", required: false },
  { key: "cf_l4", label: "CF Level 4", group: "CF-Mapping", required: false },
  { key: "cf_l5", label: "CF Level 5", group: "CF-Mapping", required: false },
  { key: "cf_mapping", label: "CF Mapping", group: "CF-Mapping", required: false },
];

// ---------------------------------------------------------------------------
// Statement-aware trimmed field set for the CoA wizard (BS/PL upload path)
// Excludes l4_sub, level_*_sort, is_ic, gl_account_id, cf_* fields.
// l6_na_mapping is relabeled to "Net assets (NA)" for brevity.
// PL adds "Reported / adjusted"; BS keeps l6_na_mapping (NA-Mapping).
// ---------------------------------------------------------------------------

/**
 * Returns the trimmed CoA field set for the wizard upload path.
 *
 * BS includes l6_na_mapping (Net assets) in the NA-Mapping group.
 * PL drops l6_na_mapping and adds reported_adjusted (Additional Information).
 * account_name is "Additional Information" in both statements.
 *
 * Field order: Core → NA-Mapping (bs only) → Additional Information.
 */
export function coaGenericFields(statement: 'bs' | 'pl'): AccountTargetField[] {
  const core: AccountTargetField[] = [
    { key: "account_number", label: "Account Number",   group: "Core", required: true,  hint: "Account number" },
    { key: "level_0",        label: "Level 0 (PL/BS)",  group: "Core", required: true,  hint: "Statement type" },
    { key: "level_1",        label: "Level 1",           group: "Core", required: true  },
    { key: "level_2",        label: "Level 2",           group: "Core", required: true  },
    { key: "level_3",        label: "Level 3",           group: "Core", required: true  },
    { key: "level_4",        label: "Level 4",           group: "Core", required: false },
  ];

  const naMapping: AccountTargetField[] = statement === 'bs'
    ? [{ key: "l6_na_mapping", label: "Net assets (NA)", group: "NA-Mapping", required: false }]
    : [];

  const additional: AccountTargetField[] = [
    { key: "account_name", label: v4Label("Account Name", "Account name"), group: "Additional Information", required: false },
    ...(statement === 'pl'
      ? [{ key: "reported_adjusted", label: "Reported / adjusted", group: "Additional Information" as AccountFieldGroup, required: false }]
      : []),
  ];

  return [...core, ...naMapping, ...additional];
}

/**
 * Backward-compatible alias — equals coaGenericFields('bs').
 * Kept so existing importers (IngestionPage, etc.) do not need changes.
 */
export const COA_GENERIC_FIELDS: AccountTargetField[] = coaGenericFields('bs');

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface AccountColumnMapperProps {
  sourceColumns: string[];
  sample: Record<string, unknown>[];
  mapping: Record<string, string>;
  onChange: (mapping: Record<string, string>) => void;
  /**
   * Override the field set rendered in the drop-zone panel.
   * Defaults to ACCOUNT_TARGET_FIELDS (full set with all groups).
   * Pass COA_GENERIC_FIELDS for the trimmed CoA-wizard upload path.
   */
  fields?: AccountTargetField[];
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

// ---------------------------------------------------------------------------
// Source chip
// ---------------------------------------------------------------------------

function SourceChip({
  col,
  sample,
  isMapped,
}: {
  col: string;
  sample: Record<string, unknown>[];
  isMapped: boolean;
}) {
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
// Drop zone
// ---------------------------------------------------------------------------

function DropZone({
  field,
  mappedCol,
  onDrop,
  onClear,
}: {
  field: AccountTargetField;
  mappedCol: string | undefined;
  onDrop: (targetKey: string, sourceCol: string) => void;
  onClear: (targetKey: string) => void;
}) {
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

  let borderClass = "border-slate-200 bg-white";
  if (over) borderClass = "border-blue-400 bg-blue-50";
  else if (isMapped) borderClass = "border-emerald-300 bg-emerald-50";
  else if (field.required) borderClass = "border-amber-300 bg-amber-50";

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`flex min-h-[44px] items-center gap-2 rounded-md border px-3 py-1.5 transition ${borderClass}`}
    >
      <div className="min-w-0 flex-1">
        <span className="text-sm font-medium text-slate-700">{field.label}</span>
        {field.required && !isMapped && (
          <span className="ml-1.5 text-xs font-semibold text-amber-600">*</span>
        )}
        {field.hint && (
          <span className="ml-2 text-xs text-slate-400">{field.hint}</span>
        )}
      </div>
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
// Main AccountColumnMapper component
// ---------------------------------------------------------------------------

export default function AccountColumnMapper({
  sourceColumns,
  sample,
  mapping,
  onChange,
  fields = ACCOUNT_TARGET_FIELDS,
}: AccountColumnMapperProps) {
  const mappedSources = new Set(Object.values(mapping));

  // Derive unique group names from the active field set in insertion order.
  const effectiveGroups: AccountFieldGroup[] = [];
  for (const f of fields) {
    if (!effectiveGroups.includes(f.group)) effectiveGroups.push(f.group);
  }

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
        <div className="max-h-[560px] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2">
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

      {/* Right panel — target field drop-zones */}
      <div className="flex-1 min-w-0">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-700">Target fields (account dimension)</h3>
          <span className="text-xs text-slate-400">
            <span className="text-amber-600 font-medium">*</span> = required
          </span>
        </div>
        <div className="max-h-[560px] overflow-y-auto space-y-5 pr-1">
          {effectiveGroups.map((group) => (
            <div key={group}>
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  {group}
                </span>
                <div className="flex-1 border-t border-slate-200" />
              </div>
              <div className="space-y-2">
                {fields.filter(f => f.group === group).map((field) => (
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

/**
 * Returns the labels of required fields that are not yet mapped.
 *
 * @param mapping  The current column mapping (target key → source column).
 * @param fields   The field set to check against. Defaults to ACCOUNT_TARGET_FIELDS
 *                 (full set). Pass COA_GENERIC_FIELDS for the CoA wizard upload path.
 */
export function missingRequiredAccountFields(
  mapping: Record<string, string>,
  fields: AccountTargetField[] = ACCOUNT_TARGET_FIELDS,
): string[] {
  return fields.filter((f) => f.required && !mapping[f.key]).map((f) => f.label);
}
