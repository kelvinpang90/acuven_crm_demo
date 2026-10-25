import { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { contactsApi } from '@/services/contacts';
import type { ContactImportResult, ImportField } from '@/types';
import ImportColumnMap, { canImportColumn, type ColumnMapping, type SheetColumn } from './ImportColumnMap';
import * as XLSX from 'xlsx';

// The only characters trimmed from both ends, the same list as the backend
// (docs/contact-import-columns.md section 9, item 16); not String.prototype.trim.
const BLANKS = ' \t\n\r\v\f 　﻿';
const MAX_HEADER_LENGTH = 100;
const MAX_SAMPLE_LENGTH = 50;
const MAX_SAMPLES = 3;

// Template header -> field, the same as the backend's rules without a column_map.
const TEMPLATE_HEADERS = new Map<string, ImportField>([
  ['name', 'name'],
  ['company', 'company'],
  ['industry', 'industry'],
  ['email', 'email'],
  ['phone', 'phone'],
  ['address', 'address'],
  ['remark', 'notes'],
  ['tag', 'tags'],
  ['assigned_to_email', 'assigned_to_email'],
  ['status', 'status'],
  ['priority', 'priority'],
  ['amount', 'deal_value'],
  ['deal_title', 'deal_title'],
  ['customer_id (add deal only)', 'id'],
]);

function stripBlanks(value: string) {
  let start = 0;
  let end = value.length;
  while (start < end && BLANKS.includes(value[start])) start += 1;
  while (end > start && BLANKS.includes(value[end - 1])) end -= 1;
  return value.slice(start, end);
}

function cut(value: string, length: number) {
  return stripBlanks(Array.from(value).slice(0, length).join(''));
}

function cellText(cell: XLSX.CellObject | undefined) {
  if (!cell) return '';
  return cell.w ?? (cell.v == null ? '' : String(cell.v));
}

// Only a plain text cell is a header; numbers, dates and formulas count as blank,
// since openpyxl reads them differently and a column_map could not match them.
function textHeader(cell: XLSX.CellObject | undefined) {
  if (!cell || cell.t !== 's' || cell.f !== undefined || 'F' in cell || typeof cell.v !== 'string') return '';
  return stripBlanks(cell.v);
}

// The sheet selected in the workbook, which is what openpyxl's `active` reads.
function activeSheet(wb: XLSX.WorkBook) {
  // SheetJS keeps the workbookView attributes but its types only declare RTL.
  const view = wb.Workbook?.Views?.[0] as unknown as { activeTab?: unknown } | undefined;
  const tab = Number(view?.activeTab ?? 0);
  const index = Number.isInteger(tab) && tab >= 0 && tab < wb.SheetNames.length ? tab : 0;
  const name = wb.SheetNames[index];
  return name === undefined ? undefined : wb.Sheets[name];
}

function readSheet(ws: XLSX.WorkSheet | undefined): { columns: SheetColumn[]; rows: string[][] } {
  const ref = ws?.['!ref'];
  if (!ws || !ref) return { columns: [], rows: [] };
  const range = XLSX.utils.decode_range(ref);
  const cellAt = (r: number, c: number) => ws[XLSX.utils.encode_cell({ r, c })] as XLSX.CellObject | undefined;

  // Columns run from A on and rows from row 2 on, whatever the sheet's range starts at.
  const rows: string[][] = [];
  for (let r = 1; r <= range.e.r; r += 1) {
    const row: string[] = [];
    for (let c = 0; c <= range.e.c; c += 1) row.push(cellText(cellAt(r, c)));
    if (row.some((value) => stripBlanks(value) !== '')) rows.push(row);
  }

  const firstByHeader = new Map<string, number>();
  const columns: SheetColumn[] = [];
  for (let c = 0; c <= range.e.c; c += 1) {
    const cell = cellAt(0, c);
    const header = textHeader(cell);
    const samples: string[] = [];
    for (const row of rows) {
      if (samples.length >= MAX_SAMPLES) break;
      const value = stripBlanks(row[c]);
      if (value) samples.push(cut(value, MAX_SAMPLE_LENGTH));
    }
    let duplicateOf: number | null = null;
    if (header) {
      duplicateOf = firstByHeader.get(header) ?? null;
      if (duplicateOf === null) firstByHeader.set(header, c);
    }
    columns.push({
      letter: XLSX.utils.encode_col(c),
      header,
      label: header || stripBlanks(cellText(cell)),
      aiHeader: header ? cut(header, MAX_HEADER_LENGTH) : '',
      samples,
      duplicateOf,
    });
  }
  return { columns, rows };
}

export default function ExcelImport() {
  const { t } = useTranslation('contacts');
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<string[][]>([]);
  const [columns, setColumns] = useState<SheetColumn[]>([]);
  const [mapping, setMapping] = useState<ColumnMapping>([]);
  const [sheetVersion, setSheetVersion] = useState(0);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ContactImportResult | null>(null);
  const [autoAssign, setAutoAssign] = useState(false);
  const [assignStrategy, setAssignStrategy] = useState('rules');

  const handleDownloadTemplate = async () => {
    try {
      const res = await contactsApi.downloadTemplate();
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'contact_import_template.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } catch { /* ignore */ }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) {
      alert('文件大小不能超过 10MB');
      return;
    }
    setFile(f);
    setResult(null);

    const reader = new FileReader();
    reader.onload = (ev) => {
      const data = new Uint8Array(ev.target?.result as ArrayBuffer);
      const wb = XLSX.read(data, { type: 'array', cellFormula: true });
      const sheet = readSheet(activeSheet(wb));
      setColumns(sheet.columns);
      setRows(sheet.rows);
      setMapping(
        sheet.columns.map((column) => (canImportColumn(column) ? TEMPLATE_HEADERS.get(column.header) ?? null : null)),
      );
      setSheetVersion((v) => v + 1);
    };
    reader.readAsArrayBuffer(f);
  };

  const nameIndex = mapping.indexOf('name');
  const idIndex = mapping.indexOf('id');
  const canImport = nameIndex >= 0 || idIndex >= 0;
  const hasValue = (row: string[], index: number) => index >= 0 && stripBlanks(row[index] ?? '') !== '';
  // A row with a customer id adds a deal to an existing customer and needs no name.
  const missingName = (row: string[]) => nameIndex >= 0 && !hasValue(row, nameIndex) && !hasValue(row, idIndex);
  const addDealRows = rows.filter((row) => hasValue(row, idIndex)).length;
  const errorRows = rows.filter(missingName);

  const handleImport = async () => {
    if (!file || !canImport) return;
    // Keyed by the stripped header; blank headers and later same-name columns are left out.
    const entries: [string, ImportField | null][] = [];
    columns.forEach((column, index) => {
      if (canImportColumn(column)) entries.push([column.header, mapping[index]]);
    });
    const columnMap = Object.fromEntries(entries);
    setImporting(true);
    try {
      const res = await contactsApi.importContacts(file, {
        autoAssign,
        assignStrategy,
        columnMap: JSON.stringify(columnMap),
      });
      setResult(res.data.data);
    } catch { /* ignore */ }
    setImporting(false);
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-3">
        <button onClick={handleDownloadTemplate} className="btn-secondary text-sm">
          {t('downloadTemplate')}
        </button>
        <button onClick={() => fileRef.current?.click()} className="btn-primary text-sm">
          {t('uploadFile')}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls"
          className="hidden"
          onChange={handleFileChange}
        />
      </div>

      {file && <p className="text-sm text-text-secondary">{file.name}</p>}

      {rows.length > 0 && (
        <>
          <ImportColumnMap key={sheetVersion} columns={columns} mapping={mapping} onChange={setMapping} />

          <div className="text-sm text-text-secondary">
            {t('importPreviewSummary', { total: rows.length, newCount: rows.length - addDealRows, dealCount: addDealRows })}
            {errorRows.length > 0 && (
              <span className="text-red-400"> {t('importPreviewMissingName', { count: errorRows.length })}</span>
            )}
          </div>

          <div className="overflow-x-auto max-h-80 border border-dark-border rounded-lg">
            <table className="w-full text-xs">
              <thead className="bg-dark-hover sticky top-0">
                <tr>
                  <th className="px-2 py-1 text-left text-text-muted">#</th>
                  {columns.slice(0, 6).map((column, c) => (
                    <th key={c} className="px-2 py-1 text-left text-text-muted">{column.label || column.letter}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 50).map((row, i) => {
                  const hasError = missingName(row);
                  return (
                    <tr key={i} className={hasError ? 'bg-red-500/10' : 'hover:bg-dark-hover'}>
                      <td className="px-2 py-1 text-text-muted">{i + 1}</td>
                      {columns.slice(0, 6).map((_, c) => (
                        <td key={c} className="px-2 py-1 text-text-secondary truncate max-w-[120px]">
                          {row[c]}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <input
                type="checkbox"
                checked={autoAssign}
                onChange={(e) => setAutoAssign(e.target.checked)}
              />
              {t('importAutoAssign')}
            </label>

            {autoAssign && (
              <div className="flex items-center gap-2 pl-6">
                <span className="text-sm text-text-secondary">{t('importAssignStrategy')}</span>
                <select
                  value={assignStrategy}
                  onChange={(e) => setAssignStrategy(e.target.value)}
                  className="input text-sm"
                >
                  <option value="rules">{t('importStrategyRules')}</option>
                  <option value="workload">{t('importStrategyWorkload')}</option>
                  <option value="win_rate">{t('importStrategyWinRate')}</option>
                  <option value="me">{t('importStrategyMe')}</option>
                </select>
              </div>
            )}

            <p className="pl-6 text-xs text-text-muted">{t('importAutoAssignHint')}</p>
          </div>

          {!canImport && <p className="text-sm text-yellow-400">{t('importNeedNameOrId')}</p>}

          <button onClick={handleImport} disabled={importing || !canImport} className="btn-primary">
            {importing ? '...' : t('confirmImport')}
          </button>
        </>
      )}

      {result && (
        <div className="card p-4 space-y-1 text-sm">
          <h4 className="font-semibold text-text-primary">{t('importResult')}</h4>
          <p className="text-text-secondary">{t('totalRows')}: {result.total}</p>
          <p className="text-green-400">{t('inserted')}: {result.inserted}</p>
          <p className="text-blue-400">{t('dealsAdded')}: {result.deals_added}</p>
          <p className="text-yellow-400">{t('skipped')}: {result.skipped}</p>
          {result.errors?.length > 0 && (
            <div className="mt-2">
              <p className="text-red-400">{t('errors')}:</p>
              {result.errors.map((e, i) => (
                <p key={i} className="text-xs text-red-300">
                  {t('importErrorRow', { row: e.row, field: e.field, message: e.message })}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
