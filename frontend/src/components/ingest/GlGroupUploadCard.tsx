/**
 * GlGroupUploadCard — v4-only (IS_DATA_UPDATE_V4) group GL upload.
 *
 * Upload ONE file covering all entities; map the Entity column plus standard GL
 * columns; commit once via entity_assignments.  Mirrors the OB combined block
 * in ProjectSetupWizard.tsx (~:5424-5449).
 *
 * This component is ONLY ever rendered when IS_DATA_UPDATE_V4 is true, so it
 * contains no v4 gate of its own.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import ColumnMapper, { glGroupTargetFields, missingRequiredFields } from './ColumnMapper';
import ValidierungStep from './ValidierungStep';
import { defaultGlOpts } from './GlEntityCard';
import type { OptionsState } from './ingestTypes';
import {
  uploadFile,
  combineGlFiles,
  budgetEntities,
  type Profile,
  type UploadResponse,
  type Dialect,
  type ValidationResponse,
  type CommitResponse,
} from '../../lib/gdpduApi';

// ---------------------------------------------------------------------------
// State type (also used by IngestionPage)
// ---------------------------------------------------------------------------

export interface GlGroupState {
  fileId?: string;
  columns?: string[];
  sample?: Record<string, unknown>[];
  dialect?: Dialect;
  entityCol?: string;
  entityColKind?: 'names' | 'prefixes';
  mapping?: Record<string, string>;
  opts?: OptionsState;
  validationOk?: boolean;
  assembledProfile?: Profile;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GlGroupUploadCardProps {
  group: GlGroupState;
  /** Fiscal years selected in the parent year-picker (first is used for combine tag). */
  years: number[];
  onPatch: (patch: Partial<GlGroupState>) => void;
  fyEndMonth?: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildEntityAssignments(
  entities: Array<{ code: string; label: string; prefix: string }>,
): Record<string, string> {
  return Object.fromEntries(
    entities
      .filter(e => e.code.trim())
      .flatMap(e => {
        const pfx = e.prefix || e.code;
        return [[e.label || e.code, pfx], [e.code, pfx]] as [string, string][];
      }),
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GlGroupUploadCard({
  group,
  years,
  onPatch,
  fyEndMonth: _fyEndMonth = 12,
}: GlGroupUploadCardProps) {
  // File upload state
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Local mapping + config (mirrors parent state for immediate UI feedback)
  const [mapping, setMapping] = useState<Record<string, string>>(group.mapping ?? {});
  const [entityColKind, setEntityColKind] = useState<'names' | 'prefixes'>(
    group.entityColKind ?? 'names',
  );

  // Options — use defaults (no options step in group card)
  const opts = group.opts ?? defaultGlOpts();

  // Entities fetched from API for entity_assignments
  const [existingEntities, setExistingEntities] = useState<
    Array<{ code: string; label: string; prefix: string }>
  >([]);

  // Pre-flight: entity values in sample not covered by entity_assignments
  const [uncoveredEntityValues, setUncoveredEntityValues] = useState<string[]>([]);

  useEffect(() => {
    budgetEntities()
      .then(r => setExistingEntities(r.entities))
      .catch(() => {});
  }, []);

  const entityAssignments = useMemo(() => buildEntityAssignments(existingEntities), [existingEntities]);

  // Derived from mapping
  const entityCol = mapping['entity'];

  // Pre-flight: recompute whenever entity col, sample, or entities change.
  // Uses functional setState so identical arrays don't trigger a re-render.
  useEffect(() => {
    if (!entityCol || !group.sample?.length) {
      setUncoveredEntityValues(prev => (prev.length === 0 ? prev : []));
      return;
    }
    const distinct = [
      ...new Set(
        group.sample.map(row => String(row[entityCol] ?? '')).filter(Boolean),
      ),
    ];
    const uncovered = distinct.filter(v => !(v in entityAssignments));
    setUncoveredEntityValues(prev =>
      prev.length === uncovered.length && prev.every((v, i) => v === uncovered[i])
        ? prev
        : uncovered,
    );
  }, [entityCol, group.sample, entityAssignments]);

  // Build GL profile for validation/commit
  function buildProfile(): Profile {
    const effectiveEntityCol = mapping['entity'];
    const entityCfg: Profile['entity'] = effectiveEntityCol
      ? { mode: 'column', value: effectiveEntityCol }
      : { mode: 'fixed', value: existingEntities[0]?.code ?? '' };

    // Strip 'entity' from columns — handled by entity: section
    const { entity: _e, ...columnMapping } = mapping;

    const amountCol = mapping['amount'] ?? opts.signAmount ?? '';

    return {
      entity: entityCfg,
      entity_assignments: entityAssignments,
      fiscal_year: { mode: 'column', value: 'fiscal_year' },
      sign:
        opts.signMode === 'signed'
          ? { mode: 'signed', amount: amountCol }
          : opts.signMode === 'soll_haben'
          ? { mode: 'soll_haben', soll: opts.signSoll, haben: opts.signHaben }
          : { mode: 'amount_dc', amount: amountCol, dc_flag: opts.signDcFlag, debit_value: opts.signDebitValue },
      decimal: opts.decimal || group.dialect?.decimal || ',',
      thousands: opts.thousands || group.dialect?.thousands || '.',
      date_dayfirst: opts.dateDayfirst,
      columns: columnMapping,
      linking_strategy: opts.linking as 'txn' | 'gegenkonto' | 'none',
      entry_type: 'actual',
    };
  }

  // Upload handler — upload → combine (appends fiscal_year column)
  async function handleFile(file: File) {
    setUploading(true);
    setUploadError(null);
    try {
      const uploaded = await uploadFile(file);
      // Tag all rows with first selected year so backend creates the fiscal_year column.
      // For multi-year group files the commit profile's fiscal_year: {mode:'column'} reads it.
      const year = years[0] ?? 0;
      const combined = await combineGlFiles({
        inputs: [{ file_id: uploaded.file_id, fiscal_year: year }],
      });
      const resetMapping = {};
      setMapping(resetMapping);
      setUncoveredEntityValues([]);
      onPatch({
        fileId: combined.file_id,
        columns: combined.columns,
        sample: combined.sample,
        dialect: combined.dialect,
        entityCol: undefined,
        mapping: resetMapping,
        validationOk: undefined,
        assembledProfile: undefined,
      });
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  function handleMappingChange(m: Record<string, string>) {
    setMapping(m);
    // Reset validation when mapping changes
    onPatch({ mapping: m, entityCol: m['entity'], validationOk: undefined, assembledProfile: undefined });
  }

  function handleEntityColKindChange(val: 'names' | 'prefixes') {
    setEntityColKind(val);
    onPatch({ entityColKind: val });
  }

  const handleEntityAssignmentsChange = useCallback(
    (_ea: Record<string, string>) => {
      // entity_assignments are derived from existing entities; no-op here
    },
    [],
  );

  const handleValidationResult = useCallback(
    (r: ValidationResponse) => {
      const profile = buildProfile();
      onPatch({
        validationOk: r.summary.passed,
        assembledProfile: profile,
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mapping, existingEntities, opts],
  );

  const noopImportSuccess = useCallback((_r: CommitResponse) => {}, []);

  const columns = group.columns ?? [];
  const sample = group.sample ?? [];
  const fields = glGroupTargetFields();
  // Use missingRequiredFields so labels reflect the active manifest (e.g. "Booking ID" in v4).
  const missingFields = missingRequiredFields(mapping, fields);
  const allRequiredMapped = missingFields.length === 0;

  // Block validation if no year selected, uncovered entity values exist, or required fields unmapped.
  const canValidate =
    Boolean(group.fileId) &&
    years.length > 0 &&
    allRequiredMapped &&
    uncoveredEntityValues.length === 0;

  const uploadResult: UploadResponse | null =
    group.fileId && columns.length > 0 && sample.length > 0 && group.dialect
      ? {
          file_id: group.fileId,
          filename: '',
          sheets: [],
          columns,
          sample,
          dialect: group.dialect,
        }
      : null;

  const glProfile = canValidate ? buildProfile() : null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm space-y-5">
      <div>
        <h3 className="text-base font-semibold text-slate-900">
          Group upload — one file for all entities
        </h3>
        <p className="mt-0.5 text-sm text-slate-500">
          Upload a single GL file containing rows for all entities. Map the Entity column so the
          backend can route each row to the correct entity via entity_assignments.
        </p>
      </div>

      {/* File drop zone */}
      <div>
        <div
          onClick={() => !group.fileId && fileInputRef.current?.click()}
          onDragOver={e => e.preventDefault()}
          onDrop={e => {
            e.preventDefault();
            const f = e.dataTransfer.files[0];
            if (f) void handleFile(f);
          }}
          className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 transition ${
            uploading
              ? 'border-blue-400 bg-blue-50 cursor-wait'
              : group.fileId
              ? 'border-emerald-300 bg-emerald-50 cursor-default'
              : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50 cursor-pointer'
          }`}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls,.csv,.txt"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0];
              if (f) void handleFile(f);
            }}
          />
          {uploading ? (
            <p className="text-xs font-medium text-blue-600">Processing…</p>
          ) : group.fileId ? (
            <div className="text-center">
              <p className="text-xs font-semibold text-emerald-700">
                File uploaded — {columns.length} columns
              </p>
              <button
                type="button"
                onClick={e => {
                  e.stopPropagation();
                  fileInputRef.current?.click();
                }}
                className="mt-1 text-xs text-slate-400 hover:text-blue-600 underline underline-offset-1"
              >
                Replace
              </button>
            </div>
          ) : (
            <p className="text-xs text-slate-400">
              Drop or click to upload a GL file covering all entities
            </p>
          )}
        </div>
        {uploadError && <p className="mt-1 text-xs text-red-600">{uploadError}</p>}
      </div>

      {/* Column mapper + entity kind + pre-flight + validation */}
      {group.fileId && columns.length > 0 && (
        <>
          <div className="space-y-2">
            <p className="text-sm font-semibold text-slate-700">Map columns to target fields</p>
            <p className="text-xs text-slate-500">
              Map the Entity column first, then the standard GL fields. Required fields (*) are
              highlighted in amber.
            </p>
            <ColumnMapper
              sourceColumns={columns}
              sample={sample}
              mapping={mapping}
              onChange={handleMappingChange}
              fields={fields}
            />
          </div>

          {/* Entity column kind — shown when entity col is mapped */}
          {entityCol && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-slate-700">The Entity column contains:</p>
              <div className="flex gap-4">
                {(
                  [
                    ['names', 'Entity names (e.g. Atlas, Calypto)'],
                    ['prefixes', 'Entity prefixes (e.g. 01, 02)'],
                  ] as const
                ).map(([val, label]) => (
                  <label
                    key={val}
                    className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer"
                  >
                    <input
                      type="radio"
                      name="glGroupEntityColKind"
                      value={val}
                      checked={entityColKind === val}
                      onChange={() => handleEntityColKindChange(val)}
                      className="accent-blue-600"
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Pre-flight guard: block commit when entity values are not in entity_assignments */}
          {uncoveredEntityValues.length > 0 && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <span className="font-medium">Entity values not covered by entity_assignments:</span>{' '}
              {uncoveredEntityValues.join(', ')}. These values cannot be routed to an entity.
              Add the missing entities to the project or correct the Entity column mapping.
            </div>
          )}

          {/* M1: sample-only disclaimer — always visible once a file is loaded */}
          <p className="text-xs text-slate-400">
            Entity coverage is checked against the file preview (first rows only). Confirm every
            entity value in your file is mapped before committing.
          </p>

          {/* Missing required fields hint — shows actual field names (v4-aware via missingFields) */}
          {!allRequiredMapped && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              <span className="font-medium">Missing required fields:</span>{' '}
              {missingFields.join(', ')}. Map all required fields (*) above to enable validation.
            </div>
          )}

          {/* B1: year guard — block validation until at least one fiscal year is selected */}
          {years.length === 0 && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              Select at least one fiscal year before validating.
            </div>
          )}

          {/* Validation */}
          {canValidate && uploadResult && glProfile && (
            <ValidierungStep
              uploadResult={uploadResult}
              profile={glProfile}
              onResult={handleValidationResult}
              onEntityAssignmentsChange={handleEntityAssignmentsChange}
              onImportSuccess={noopImportSuccess}
              stagingMode
            />
          )}
        </>
      )}
    </div>
  );
}
