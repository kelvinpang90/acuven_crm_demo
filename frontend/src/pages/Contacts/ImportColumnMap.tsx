import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { aiApi } from '@/services/ai';
import type { ApiError, ImportField } from '@/types';

// The fields in the order the dropdown lists them (docs/contact-import-columns.md section 2).
const IMPORT_FIELDS: ImportField[] = [
  'name',
  'company',
  'industry',
  'email',
  'phone',
  'address',
  'notes',
  'tags',
  'assigned_to_email',
  'status',
  'priority',
  'deal_value',
  'deal_title',
  'id',
];

const MAX_AI_COLUMNS = 40;

/** One column of the sheet's first row, as read by ExcelImport. */
export interface SheetColumn {
  letter: string;
  /** Text header stripped of the nine blank characters; '' for blank or non-text cells. */
  header: string;
  /** What the preview shows for the header cell. */
  label: string;
  /** `header` cut to 100 code points and stripped again: what is sent to the AI. */
  aiHeader: string;
  /** Up to three non-blank display texts from row 2 on, each cut to 50 code points. */
  samples: string[];
  /** Index of the first column with the same header, or null. */
  duplicateOf: number | null;
}

export type ColumnMapping = (ImportField | null)[];

interface Props {
  columns: SheetColumn[];
  mapping: ColumnMapping;
  onChange: (mapping: ColumnMapping) => void;
}

function outputLanguage(language: string): 'zh-CN' | 'en' | undefined {
  if (language.startsWith('zh')) return 'zh-CN';
  if (language.startsWith('en')) return 'en';
  return undefined;
}

function errorMessage(error: unknown, t: TFunction<'contacts'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('aiTemporarilyUnavailable');
  switch (error.response.data?.error?.code) {
    case 'AI_NOT_CONFIGURED': return t('aiNotEnabled');
    case 'AI_UNAVAILABLE': return t('aiTemporarilyUnavailable');
    case 'AI_ERROR': return t('aiError');
    default: return t('aiRequestFailed');
  }
}

export function canImportColumn(column: SheetColumn) {
  return column.header !== '' && column.duplicateOf === null;
}

export default function ImportColumnMap({ columns, mapping, onChange }: Props) {
  const { t, i18n } = useTranslation('contacts');
  const [aiEnabled, setAiEnabled] = useState(false);
  const [recognizing, setRecognizing] = useState(false);
  const [aiFilled, setAiFilled] = useState<boolean[]>(() => columns.map(() => false));
  // Columns the user has changed by hand; AI never fills them, even back to "do not import".
  const touched = useRef<boolean[]>(columns.map(() => false));
  const latestMapping = useRef(mapping);
  const mounted = useRef(true);

  latestMapping.current = mapping;

  useEffect(() => {
    mounted.current = true;
    let active = true;
    void aiApi.getStatus().then(
      (status) => { if (active) setAiEnabled(status.enabled); },
      () => { if (active) setAiEnabled(false); },
    );
    return () => {
      active = false;
      mounted.current = false;
    };
  }, []);

  // A group of headers that are the same once cut is sent once, as its first column.
  const aiColumns = useMemo(() => {
    const seen = new Set<string>();
    const picked: number[] = [];
    columns.forEach((column, index) => {
      if (!column.aiHeader || seen.has(column.aiHeader)) return;
      seen.add(column.aiHeader);
      picked.push(index);
    });
    return picked.slice(0, MAX_AI_COLUMNS);
  }, [columns]);

  const change = (index: number, value: string) => {
    touched.current[index] = true;
    setAiFilled((prev) => prev.map((filled, i) => (i === index ? false : filled)));
    const next = [...mapping];
    next[index] = IMPORT_FIELDS.find((field) => field === value) ?? null;
    onChange(next);
  };

  const recognize = async () => {
    if (recognizing || aiColumns.length === 0) return;
    setRecognizing(true);
    try {
      const response = await aiApi.suggestImportColumns(
        aiColumns.map((index) => ({ header: columns[index].aiHeader, samples: columns[index].samples })),
        outputLanguage(i18n.language),
      );
      if (!mounted.current) return;
      const indexByHeader = new Map(aiColumns.map((index) => [columns[index].aiHeader, index]));
      const suggested = new Map<number, ImportField>();
      for (const { header, field } of response.data.data.mappings) {
        const index = indexByHeader.get(header);
        if (index !== undefined && field !== null && IMPORT_FIELDS.includes(field)) suggested.set(index, field);
      }
      const next = [...latestMapping.current];
      const filled = new Set<number>();
      for (const index of aiColumns) {
        const field = suggested.get(index);
        if (!field || next[index] !== null || touched.current[index] || next.includes(field)) continue;
        next[index] = field;
        filled.add(index);
      }
      if (filled.size === 0) {
        toast(t('importAiNothingFilled'));
        return;
      }
      setAiFilled((prev) => prev.map((value, i) => value || filled.has(i)));
      onChange(next);
    } catch (error: unknown) {
      if (mounted.current) toast.error(errorMessage(error, t));
    } finally {
      if (mounted.current) setRecognizing(false);
    }
  };

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-text-primary">{t('importColumnMapTitle')}</p>
        {aiEnabled && (
          <button
            type="button"
            className="btn-secondary text-xs"
            disabled={recognizing || aiColumns.length === 0}
            onClick={recognize}
          >
            {recognizing ? t('importAiRecognizing') : t('importAiRecognize')}
          </button>
        )}
      </div>

      <div className="overflow-x-auto max-h-80 border border-dark-border rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-dark-hover sticky top-0">
            <tr>
              <th className="px-2 py-1 text-left text-text-muted">{t('importColumnMapColumn')}</th>
              <th className="px-2 py-1 text-left text-text-muted">{t('importColumnMapField')}</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((column, index) => {
              const importable = canImportColumn(column);
              return (
                <tr key={index} className="hover:bg-dark-hover">
                  <td className="px-2 py-1 text-text-secondary">
                    <span className="mr-2 text-text-muted">{column.letter}</span>
                    {column.label}
                  </td>
                  <td className="px-2 py-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <select
                        value={importable ? mapping[index] ?? '' : ''}
                        disabled={!importable}
                        onChange={(e) => change(index, e.target.value)}
                        className="input text-xs"
                      >
                        <option value="">{t('importColumnSkip')}</option>
                        {IMPORT_FIELDS.map((field) => (
                          <option
                            key={field}
                            value={field}
                            disabled={mapping.some((used, i) => i !== index && used === field)}
                          >
                            {t(`importFields.${field}`)}
                          </option>
                        ))}
                      </select>
                      {aiFilled[index] && (
                        <span className="rounded px-1 text-[10px] font-medium text-blue-400 border border-blue-400/40">
                          {t('importAiBadge')}
                        </span>
                      )}
                      {!column.header && <span className="text-text-muted">{t('importColumnNoHeader')}</span>}
                      {column.duplicateOf !== null && (
                        <span className="text-yellow-400">
                          {t('importColumnDuplicate', { column: columns[column.duplicateOf].letter })}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
