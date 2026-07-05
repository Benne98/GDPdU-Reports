/**
 * PartnerMasterEditor.tsx
 *
 * Reusable inline editor for customer or supplier master data.
 * Renders a paginated, searchable table with inline edit + delete,
 * an "Add" form, and a bulk-upload affordance linking to the existing
 * uploadPartnerMaster / commitPartnerMaster flow.
 *
 * Props:
 *   side     — 'customers' | 'suppliers'
 *   compact  — optional, tightens padding for embedded use
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listMasters,
  createMaster,
  updateMaster,
  deleteMaster,
  mastersRef,
  uploadPartnerMaster,
  commitPartnerMaster,
  type CustomerDetail,
  type SupplierDetail,
  type MasterDetail,
  type MastersRefResponse,
  type CreateMasterBody,
  type PatchMasterBody,
  type DeleteMasterConflict,
} from '../../lib/gdpduApi';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NAVY = '#1E3A5F';
const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Small UI primitives (inline to avoid new files)
// ---------------------------------------------------------------------------

function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/80 px-4 py-3 text-sm text-blue-900">
      {children}
    </div>
  );
}

function ErrorBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      {children}
    </div>
  );
}

function SuccessBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
      {children}
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="animate-spin h-4 w-4"
      style={{ color: NAVY }}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Type helpers
// ---------------------------------------------------------------------------

function isSupplier(row: MasterDetail): row is SupplierDetail {
  return 'supplier_id' in row;
}

function rowId(row: MasterDetail): string {
  return isSupplier(row) ? row.supplier_id : (row as CustomerDetail).customer_id;
}

function rowNumber(row: MasterDetail): string {
  return isSupplier(row) ? row.creditor_number : (row as CustomerDetail).debtor_number;
}

function formatDate(iso?: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Delete confirm dialog
// ---------------------------------------------------------------------------

interface DeleteConflictInfo {
  message: string;
  usage: number;
  total: number;
}

function DeleteConfirmDialog({
  id,
  name,
  conflict,
  onConfirm,
  onForce,
  onCancel,
  deleting,
}: {
  id: string;
  name: string;
  conflict: DeleteConflictInfo | null;
  onConfirm: () => void;
  onForce: () => void;
  onCancel: () => void;
  deleting: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl border border-slate-200 p-6 max-w-md w-full mx-4">
        <h3 className="text-base font-semibold text-slate-900 mb-2">
          Delete {name}
        </h3>
        {conflict ? (
          <div className="space-y-3">
            <ErrorBox>
              <p className="font-semibold">{conflict.message}</p>
              <p className="mt-1">
                This record is referenced by <strong>{conflict.usage}</strong> fact
                row{conflict.usage !== 1 ? 's' : ''} (total {conflict.total}).
                Deleting it will leave those rows without a master record.
              </p>
            </ErrorBox>
            <p className="text-sm text-slate-600">
              You can force-delete anyway, which will remove the master record but
              leave the GL/sales facts untouched.
            </p>
          </div>
        ) : (
          <p className="text-sm text-slate-600">
            Are you sure you want to delete record <span className="font-mono font-semibold">{id}</span>?
            This action cannot be undone.
          </p>
        )}
        <div className="mt-5 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={deleting}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            Cancel
          </button>
          {conflict ? (
            <button
              type="button"
              onClick={onForce}
              disabled={deleting}
              className="rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-40"
            >
              {deleting ? 'Deleting…' : 'Force delete'}
            </button>
          ) : (
            <button
              type="button"
              onClick={onConfirm}
              disabled={deleting}
              className="rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-40"
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row-edit modal
// ---------------------------------------------------------------------------

function EditModal({
  row,
  side,
  countries,
  onSave,
  onCancel,
}: {
  row: MasterDetail;
  side: 'customers' | 'suppliers';
  countries: { code: string; name: string }[];
  onSave: (patch: PatchMasterBody) => Promise<void>;
  onCancel: () => void;
}) {
  const [fields, setFields] = useState<PatchMasterBody>({
    name_line_1: row.name_line_1,
    name_line_2: row.name_line_2 ?? '',
    country_code: row.country_code ?? '',
    region_code: row.region_code ?? '',
    city: row.city ?? '',
    postal_code: row.postal_code ?? '',
    default_currency: row.default_currency ?? '',
    purchasing_org: isSupplier(row) ? (row.purchasing_org ?? '') : undefined,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set(key: keyof PatchMasterBody, val: string) {
    setFields(prev => ({ ...prev, [key]: val }));
  }

  async function handleSave() {
    const name = fields.name_line_1?.trim() ?? '';
    if (!name) {
      setError('Name (line 1) is required.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Build clean patch: only include fields that have a non-empty value.
      // name_line_1 is always included. All other optional strings are only
      // included when non-empty so the backend never sees '' for varchar fields.
      const clean: PatchMasterBody = { name_line_1: name };
      const optional: Array<keyof Omit<PatchMasterBody, 'name_line_1'>> = [
        'name_line_2', 'country_code', 'region_code', 'city', 'postal_code',
        'default_currency', 'purchasing_org',
      ];
      for (const k of optional) {
        const v = fields[k];
        if (typeof v === 'string' && v.trim() !== '') {
          clean[k] = v.trim();
        }
      }
      await onSave(clean);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  }

  const inputCls = 'w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl border border-slate-200 p-6 max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
        <h3 className="text-base font-semibold text-slate-900 mb-4">
          Edit {side === 'customers' ? 'Customer' : 'Supplier'}
        </h3>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Name (line 1) <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={fields.name_line_1 ?? ''}
              onChange={e => set('name_line_1', e.target.value)}
              className={inputCls}
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Name (line 2)</label>
            <input type="text" value={fields.name_line_2 ?? ''} onChange={e => set('name_line_2', e.target.value)} className={inputCls} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Country</label>
              {countries.length > 0 ? (
                <select value={fields.country_code ?? ''} onChange={e => set('country_code', e.target.value)} className={inputCls}>
                  <option value="">— None —</option>
                  {countries.map(c => (
                    <option key={c.code} value={c.code}>{c.code} — {c.name}</option>
                  ))}
                </select>
              ) : (
                <input type="text" value={fields.country_code ?? ''} onChange={e => set('country_code', e.target.value)} className={inputCls} placeholder="e.g. DE" />
              )}
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Region</label>
              <input type="text" value={fields.region_code ?? ''} onChange={e => set('region_code', e.target.value)} className={inputCls} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">City</label>
              <input type="text" value={fields.city ?? ''} onChange={e => set('city', e.target.value)} className={inputCls} />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Postal code</label>
              <input type="text" value={fields.postal_code ?? ''} onChange={e => set('postal_code', e.target.value)} className={inputCls} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Default currency</label>
              <input type="text" value={fields.default_currency ?? ''} onChange={e => set('default_currency', e.target.value)} className={inputCls} placeholder="EUR" />
            </div>
            {side === 'suppliers' && (
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Purchasing org</label>
                <input type="text" value={fields.purchasing_org ?? ''} onChange={e => set('purchasing_org', e.target.value)} className={inputCls} />
              </div>
            )}
          </div>
          {error && <ErrorBox>{error}</ErrorBox>}
        </div>
        <div className="mt-6 flex items-center justify-end gap-3">
          <button type="button" onClick={onCancel} disabled={saving}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40">
            Cancel
          </button>
          <button type="button" onClick={() => void handleSave()} disabled={saving}
            className="rounded-md px-5 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40"
            style={{ backgroundColor: NAVY }}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add form
// ---------------------------------------------------------------------------

function AddForm({
  side,
  refData,
  onAdded,
}: {
  side: 'customers' | 'suppliers';
  refData: MastersRefResponse;
  onAdded: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [fields, setFields] = useState<CreateMasterBody>({
    entity_prefix: '',
    number: '',
    name_line_1: '',
    name_line_2: '',
    country_code: '',
    region_code: '',
    city: '',
    postal_code: '',
    default_currency: 'EUR',
    purchasing_org: '',
    source_system: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function set(key: keyof CreateMasterBody, val: string) {
    setFields(prev => ({ ...prev, [key]: val }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!fields.entity_prefix.trim()) { setError('Entity prefix is required.'); return; }
    if (!fields.number.trim()) { setError('Number is required.'); return; }
    if (!fields.name_line_1.trim()) { setError('Name (line 1) is required.'); return; }
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      // strip empty optionals
      const body: CreateMasterBody = {
        entity_prefix: fields.entity_prefix.trim(),
        number: fields.number.trim(),
        name_line_1: fields.name_line_1.trim(),
      };
      if (fields.name_line_2?.trim()) body.name_line_2 = fields.name_line_2.trim();
      if (fields.country_code?.trim()) body.country_code = fields.country_code.trim();
      if (fields.region_code?.trim()) body.region_code = fields.region_code.trim();
      if (fields.city?.trim()) body.city = fields.city.trim();
      if (fields.postal_code?.trim()) body.postal_code = fields.postal_code.trim();
      if (fields.default_currency?.trim()) body.default_currency = fields.default_currency.trim();
      if (side === 'suppliers' && fields.purchasing_org?.trim()) body.purchasing_org = fields.purchasing_org.trim();
      if (fields.source_system?.trim()) body.source_system = fields.source_system.trim();

      await createMaster(side, body);
      setSuccess(`${side === 'customers' ? 'Customer' : 'Supplier'} added successfully.`);
      setFields({ entity_prefix: fields.entity_prefix, number: '', name_line_1: '', name_line_2: '', country_code: '', region_code: '', city: '', postal_code: '', default_currency: 'EUR', purchasing_org: '', source_system: '' });
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add record');
    } finally {
      setSaving(false);
    }
  }

  const inputCls = 'w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => { setOpen(v => !v); setError(null); setSuccess(null); }}
        className="flex w-full items-center justify-between px-5 py-4 text-sm font-semibold text-slate-800 hover:bg-slate-50 rounded-xl transition"
      >
        <span>Add {side === 'customers' ? 'customer' : 'supplier'}</span>
        <span className="text-slate-400 text-base">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <form onSubmit={e => void handleSubmit(e)} className="border-t border-slate-100 px-5 py-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">
                Entity prefix <span className="text-red-500">*</span>
              </label>
              {refData.entity_prefixes.length > 0 ? (
                <select value={fields.entity_prefix} onChange={e => set('entity_prefix', e.target.value)} className={inputCls} required>
                  <option value="">— Select —</option>
                  {refData.entity_prefixes.map(ep => (
                    <option key={ep.code} value={ep.prefix}>{ep.prefix} — {ep.name}</option>
                  ))}
                </select>
              ) : (
                <input type="text" maxLength={2} value={fields.entity_prefix} onChange={e => set('entity_prefix', e.target.value)} className={inputCls} placeholder="e.g. 01" required />
              )}
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">
                {side === 'customers' ? 'Debtor' : 'Creditor'} number <span className="text-red-500">*</span>
              </label>
              <input type="text" value={fields.number} onChange={e => set('number', e.target.value)} className={inputCls} placeholder="e.g. 10000" required />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">
                Name (line 1) <span className="text-red-500">*</span>
              </label>
              <input type="text" value={fields.name_line_1} onChange={e => set('name_line_1', e.target.value)} className={inputCls} placeholder="Company name" required />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Name (line 2)</label>
              <input type="text" value={fields.name_line_2 ?? ''} onChange={e => set('name_line_2', e.target.value)} className={inputCls} />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Country</label>
              {refData.countries.length > 0 ? (
                <select value={fields.country_code ?? ''} onChange={e => set('country_code', e.target.value)} className={inputCls}>
                  <option value="">— None —</option>
                  {refData.countries.map(c => (
                    <option key={c.code} value={c.code}>{c.code} — {c.name}</option>
                  ))}
                </select>
              ) : (
                <input type="text" maxLength={2} value={fields.country_code ?? ''} onChange={e => set('country_code', e.target.value)} className={inputCls} placeholder="e.g. DE" />
              )}
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">City</label>
              <input type="text" value={fields.city ?? ''} onChange={e => set('city', e.target.value)} className={inputCls} />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Postal code</label>
              <input type="text" value={fields.postal_code ?? ''} onChange={e => set('postal_code', e.target.value)} className={inputCls} />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Default currency</label>
              <input type="text" maxLength={3} value={fields.default_currency ?? ''} onChange={e => set('default_currency', e.target.value.toUpperCase())} className={inputCls} placeholder="EUR" />
            </div>
            {side === 'suppliers' && (
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Purchasing org</label>
                <input type="text" value={fields.purchasing_org ?? ''} onChange={e => set('purchasing_org', e.target.value)} className={inputCls} />
              </div>
            )}
          </div>

          {error && <ErrorBox>{error}</ErrorBox>}
          {success && <SuccessBox>{success}</SuccessBox>}

          <div className="flex items-center justify-end gap-3 pt-1">
            <button type="button" onClick={() => setOpen(false)} disabled={saving}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="rounded-md px-5 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40"
              style={{ backgroundColor: NAVY }}>
              {saving ? 'Saving…' : 'Add record'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bulk-upload affordance
// ---------------------------------------------------------------------------

function BulkUploadSection({ side }: { side: 'customers' | 'suppliers' }) {
  const [uploading, setUploading] = useState(false);
  const [uploadedFileId, setUploadedFileId] = useState<string | null>(null);
  const [uploadedColumns, setUploadedColumns] = useState<string[]>([]);
  const [joinKeyCol, setJoinKeyCol] = useState('');
  const [nameCol, setNameCol] = useState('');
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File) {
    setUploading(true);
    setError(null);
    setUploadedFileId(null);
    setUploadedColumns([]);
    try {
      const res = await uploadPartnerMaster(file);
      setUploadedFileId(res.file_id);
      setUploadedColumns(res.columns);
      const jk = res.columns.find(c => /debtor|creditor|kred|deb|partner.?no|kunden.?nr|lief.?nr|number/i.test(c)) ?? '';
      const nc = res.columns.find(c => /name|firma|bezeichnung/i.test(c)) ?? '';
      setJoinKeyCol(jk);
      setNameCol(nc);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  async function handleCommit() {
    if (!uploadedFileId || !joinKeyCol || !nameCol) return;
    setCommitting(true);
    setError(null);
    setSuccess(null);
    try {
      const r = await commitPartnerMaster({
        file_id: uploadedFileId,
        profile: {
          side: side === 'customers' ? 'customer' : 'supplier',
          entity: { mode: 'fixed', value: '' },
          join_key: { column: joinKeyCol },
          columns: { name_line_1: nameCol },
        },
      });
      setSuccess(`${r.upserted} ${r.side === 'customer' ? 'customers' : 'suppliers'} upserted.`);
      setUploadedFileId(null);
      setUploadedColumns([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Commit failed');
    } finally {
      setCommitting(false);
    }
  }

  const selectCls = 'w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => { setOpen(v => !v); setError(null); setSuccess(null); }}
        className="flex w-full items-center justify-between px-5 py-4 text-sm font-semibold text-slate-800 hover:bg-slate-50 rounded-xl transition"
      >
        <span>Bulk import from file</span>
        <span className="text-slate-400 text-base">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="border-t border-slate-100 px-5 py-5 space-y-4">
          <InfoBox>
            Upload an XLSX/CSV file. Map the partner-number column (join key) and
            the name column — the backend will upsert all rows into the master table.
          </InfoBox>

          <div
            onClick={() => fileRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) void handleFile(f); }}
            className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-8 cursor-pointer transition ${uploading ? 'border-blue-400 bg-blue-50' : uploadedFileId ? 'border-emerald-300 bg-emerald-50' : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'}`}
          >
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv,.txt" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) void handleFile(f); }} />
            {uploading ? (
              <span className="text-sm text-blue-600 font-medium flex items-center gap-2"><Spinner /> Processing…</span>
            ) : uploadedFileId ? (
              <div className="text-center">
                <p className="text-sm font-semibold text-emerald-700">File ready</p>
                <p className="text-xs text-slate-500 mt-0.5">{uploadedColumns.length} columns detected — click to replace</p>
              </div>
            ) : (
              <>
                <p className="text-sm font-semibold text-slate-700">Drop a file or click to browse</p>
                <p className="text-xs text-slate-400 mt-0.5">XLSX · XLS · CSV</p>
              </>
            )}
          </div>

          {uploadedFileId && uploadedColumns.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">
                  {side === 'customers' ? 'Debtor' : 'Creditor'} number column (join key) <span className="text-red-500">*</span>
                </label>
                <select value={joinKeyCol} onChange={e => setJoinKeyCol(e.target.value)} className={selectCls}>
                  <option value="">— Select —</option>
                  {uploadedColumns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">
                  Name column <span className="text-red-500">*</span>
                </label>
                <select value={nameCol} onChange={e => setNameCol(e.target.value)} className={selectCls}>
                  <option value="">— Select —</option>
                  {uploadedColumns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>
          )}

          {error && <ErrorBox>{error}</ErrorBox>}
          {success && <SuccessBox>{success}</SuccessBox>}

          {uploadedFileId && (
            <div className="flex items-center justify-end">
              <button type="button" onClick={() => void handleCommit()}
                disabled={committing || !joinKeyCol || !nameCol}
                className="rounded-md px-5 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40"
                style={{ backgroundColor: NAVY }}>
                {committing ? 'Importing…' : 'Commit import'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main PartnerMasterEditor component
// ---------------------------------------------------------------------------

interface PartnerMasterEditorProps {
  side: 'customers' | 'suppliers';
  compact?: boolean;
  /** When false, hides the bulk CSV/Excel upload section. Defaults to true. */
  showBulkImport?: boolean;
}

export default function PartnerMasterEditor({ side, compact = false, showBulkImport = true }: PartnerMasterEditorProps) {
  // List state
  const [rows, setRows] = useState<MasterDetail[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [refData, setRefData] = useState<MastersRefResponse>({ entity_prefixes: [], countries: [] });

  // Edit state
  const [editRow, setEditRow] = useState<MasterDetail | null>(null);

  // Delete state
  const [deleteRow, setDeleteRow] = useState<MasterDetail | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<DeleteMasterConflict | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Success feedback
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Debounced search
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState('');

  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(0);
    }, 300);
    return () => { if (searchTimer.current) clearTimeout(searchTimer.current); };
  }, [search]);

  // Load ref data once
  useEffect(() => {
    mastersRef()
      .then(setRefData)
      .catch(() => { /* silently ignore — dropdowns fall back to text inputs */ });
  }, []);

  // Load list
  const loadList = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const res = await listMasters({
        side,
        search: debouncedSearch || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setRows(res.rows);
      setTotal(res.total);
    } catch (e) {
      setListError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [side, debouncedSearch, page]);

  useEffect(() => { void loadList(); }, [loadList]);

  // Flash success for 3s
  function flash(msg: string) {
    setSuccessMsg(msg);
    setTimeout(() => setSuccessMsg(null), 3000);
  }

  // Edit handler
  async function handleSaveEdit(patch: PatchMasterBody) {
    if (!editRow) return;
    const updated = await updateMaster(side, rowId(editRow), patch);
    setRows(prev => prev.map(r => rowId(r) === rowId(updated) ? updated : r));
    setEditRow(null);
    flash('Record updated successfully.');
  }

  // Delete initiation
  async function handleDeleteClick(row: MasterDetail) {
    setDeleteRow(row);
    setDeleteConflict(null);
    setDeleting(false);
  }

  async function handleDeleteConfirm() {
    if (!deleteRow) return;
    setDeleting(true);
    try {
      await deleteMaster(side, rowId(deleteRow), false);
      setDeleteRow(null);
      flash('Record deleted.');
      void loadList();
    } catch (e) {
      const err = e as Error & { conflict?: DeleteMasterConflict };
      if (err.conflict) {
        setDeleteConflict(err.conflict);
      } else {
        setDeleteRow(null);
        flash(`Delete failed: ${err.message}`);
      }
    } finally {
      setDeleting(false);
    }
  }

  async function handleForceDelete() {
    if (!deleteRow) return;
    setDeleting(true);
    try {
      await deleteMaster(side, rowId(deleteRow), true);
      setDeleteRow(null);
      setDeleteConflict(null);
      flash('Record force-deleted.');
      void loadList();
    } catch (e) {
      setDeleteRow(null);
      setDeleteConflict(null);
      flash(`Force delete failed: ${(e as Error).message}`);
    } finally {
      setDeleting(false);
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const sideLabel = side === 'customers' ? 'Customers' : 'Suppliers';
  const p = compact ? 'py-2' : 'py-6';

  return (
    <div className={`space-y-5 ${p}`}>

      {/* Header + search */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <p className="text-sm text-slate-500 mt-0.5">
            {total > 0 ? `${total.toLocaleString('en-US')} record${total !== 1 ? 's' : ''}` : 'No records yet'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={`Search ${sideLabel.toLowerCase()}…`}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm w-56 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          {loading && <Spinner />}
        </div>
      </div>

      {/* Success flash */}
      {successMsg && <SuccessBox>{successMsg}</SuccessBox>}

      {/* List error */}
      {listError && <ErrorBox>{listError}</ErrorBox>}

      {/* Table */}
      {!listError && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          {rows.length === 0 && !loading ? (
            <div className="px-6 py-10 text-center text-sm text-slate-400">
              {debouncedSearch ? `No ${sideLabel.toLowerCase()} match "${debouncedSearch}".` : `No ${sideLabel.toLowerCase()} loaded yet. Add one below or use bulk import.`}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-slate-50 border-b border-slate-200">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">ID</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Entity</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Country</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Updated</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide w-20">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map(row => (
                    <tr key={rowId(row)} className="hover:bg-slate-50/50 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-slate-600 whitespace-nowrap">
                        <span className="font-semibold text-slate-800">{rowNumber(row)}</span>
                        <span className="text-slate-400 ml-1">({rowId(row)})</span>
                      </td>
                      <td className="px-4 py-3 text-slate-900">
                        <div className="font-medium truncate max-w-[200px]" title={row.name_line_1}>{row.name_line_1}</div>
                        {row.name_line_2 && (
                          <div className="text-xs text-slate-400 truncate max-w-[200px]">{row.name_line_2}</div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-600 whitespace-nowrap">{row.entity_prefix || '—'}</td>
                      <td className="px-4 py-3 text-slate-600 whitespace-nowrap">{row.country_code || '—'}</td>
                      <td className="px-4 py-3 text-slate-400 text-xs whitespace-nowrap">{formatDate(row.updated_at)}</td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            title="Edit"
                            onClick={() => setEditRow(row)}
                            className="rounded p-1 text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition"
                          >
                            {/* Pencil icon */}
                            <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536M9 11l6-6 3 3-6 6H9v-3z" />
                            </svg>
                          </button>
                          <button
                            type="button"
                            title="Delete"
                            onClick={() => void handleDeleteClick(row)}
                            className="rounded p-1 text-slate-400 hover:text-red-600 hover:bg-red-50 transition"
                          >
                            {/* Trash icon */}
                            <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-slate-100 bg-slate-50/50">
              <span className="text-xs text-slate-500">
                Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPage(p => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                >
                  Previous
                </button>
                <span className="px-2 text-xs text-slate-500">{page + 1} / {totalPages}</span>
                <button
                  type="button"
                  onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Add form */}
      <AddForm side={side} refData={refData} onAdded={() => { setPage(0); void loadList(); }} />

      {/* Bulk upload */}
      {showBulkImport && <BulkUploadSection side={side} />}

      {/* Edit modal */}
      {editRow && (
        <EditModal
          row={editRow}
          side={side}
          countries={refData.countries}
          onSave={handleSaveEdit}
          onCancel={() => setEditRow(null)}
        />
      )}

      {/* Delete confirm dialog */}
      {deleteRow && (
        <DeleteConfirmDialog
          id={rowId(deleteRow)}
          name={deleteRow.name_line_1}
          conflict={deleteConflict}
          onConfirm={() => void handleDeleteConfirm()}
          onForce={() => void handleForceDelete()}
          onCancel={() => { setDeleteRow(null); setDeleteConflict(null); }}
          deleting={deleting}
        />
      )}
    </div>
  );
}
