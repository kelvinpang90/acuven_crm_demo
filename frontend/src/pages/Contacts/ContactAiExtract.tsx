import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { aiApi } from '@/services/ai';
import { contactsApi } from '@/services/contacts';
import type { AiContactExtractField, ApiError, Contact } from '@/types';

// Same options and email format as ContactForm.tsx.
const INDUSTRIES = [
  '科技/IT', '金融/保险', '医疗/健康', '教育/培训', '零售/电商',
  '制造/工业', '房地产', '咨询/服务', '餐饮/酒店', '物流/供应链',
];
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// Same rule as the backend: a name made only of digits, plus signs, spaces and dashes is a phone number.
const PHONE_NAME_PATTERN = /^[0-9+ -]+$/;

interface Props {
  contactId: string;
  onUpdated?: () => void;
}

interface Row {
  field: AiContactExtractField;
  value: string;
  evidence: string;
  checked: boolean;
  hasValue: boolean;
}

interface ExtractResult {
  contactId: string;
  truncated: boolean;
  rows: Row[];
}

function outputLanguage(language: string): 'zh-CN' | 'en' | undefined {
  if (language.startsWith('zh')) return 'zh-CN';
  if (language.startsWith('en')) return 'en';
  return undefined;
}

function errorMessage(error: unknown, t: TFunction<'contacts'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('aiTemporarilyUnavailable');
  switch (error.response.data?.error?.code) {
    case 'NOT_ASSIGNED': return t('aiExtractNotAssigned');
    case 'NOTHING_TO_FILL': return t('aiExtractNothingToFill');
    case 'NO_MESSAGES': return t('aiExtractNoMessages');
    case 'AI_NOT_CONFIGURED': return t('aiNotEnabled');
    case 'AI_UNAVAILABLE': return t('aiTemporarilyUnavailable');
    default: return t('aiRequestFailed');
  }
}

function saveErrorMessage(error: unknown, t: TFunction<'contacts'>) {
  const message = axios.isAxiosError<ApiError>(error) ? error.response?.data?.error?.message : undefined;
  return message ? t('aiExtractSaveFailedWithMessage', { message }) : t('aiExtractSaveFailed');
}

function isFieldOpen(contact: Contact, field: AiContactExtractField) {
  if (field === 'name') {
    const name = contact.name.trim();
    return PHONE_NAME_PATTERN.test(name) && /[0-9]/.test(name);
  }
  return !contact[field]?.trim();
}

function invalidEmail(row: Row) {
  return row.field === 'email' && !EMAIL_PATTERN.test(row.value.trim());
}

function isBlocked(row: Row) {
  return row.hasValue || !row.value.trim() || invalidEmail(row);
}

export default function ContactAiExtract({ contactId, onUpdated }: Props) {
  const { t, i18n } = useTranslation('contacts');
  const [aiEnabled, setAiEnabled] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<ExtractResult | null>(null);
  const activeContactId = useRef(contactId);
  const requestVersion = useRef(0);

  activeContactId.current = contactId;

  useEffect(() => {
    let active = true;
    void aiApi.getStatus().then(
      (status) => { if (active) setAiEnabled(status.enabled); },
      () => { if (active) setAiEnabled(false); },
    );
    return () => { active = false; };
  }, []);

  useEffect(() => {
    requestVersion.current += 1;
    setGenerating(false);
    setSaving(false);
    setResult(null);
    return () => { requestVersion.current += 1; };
  }, [contactId]);

  const isCurrent = (requestedContactId: string, version: number) =>
    activeContactId.current === requestedContactId && requestVersion.current === version;

  const generate = async () => {
    if (generating || saving) return;
    const requestedContactId = contactId;
    const version = ++requestVersion.current;
    setGenerating(true);
    setResult(null);
    try {
      const response = await aiApi.createContactExtract(requestedContactId, outputLanguage(i18n.language));
      if (isCurrent(requestedContactId, version)) {
        const data = response.data.data;
        setResult({
          contactId: requestedContactId,
          truncated: data.truncated,
          rows: data.suggestions.map((s) => {
            const row = { field: s.field, value: s.value, evidence: s.evidence, checked: true, hasValue: false };
            return { ...row, checked: !isBlocked(row) };
          }),
        });
      }
    } catch (error: unknown) {
      if (isCurrent(requestedContactId, version)) toast.error(errorMessage(error, t));
    } finally {
      if (isCurrent(requestedContactId, version)) setGenerating(false);
    }
  };

  const updateRow = (index: number, change: Partial<Row>) => {
    setResult((current) => {
      if (!current) return current;
      const rows = current.rows.map((row, i) => {
        if (i !== index) return row;
        const next = { ...row, ...change };
        return isBlocked(next) ? { ...next, checked: false } : next;
      });
      return { ...current, rows };
    });
  };

  const fill = async () => {
    if (!result || saving || generating) return;
    const requestedContactId = result.contactId;
    const version = requestVersion.current;
    setSaving(true);
    try {
      const fresh = (await contactsApi.getContact(requestedContactId)).data.data;
      if (!isCurrent(requestedContactId, version)) return;
      const rows = result.rows.map((row) => {
        if (!row.checked || isBlocked(row) || isFieldOpen(fresh, row.field)) return row;
        return { ...row, hasValue: true, checked: false };
      });
      setResult({ ...result, rows });
      const payload: Partial<Record<AiContactExtractField, string>> = {};
      for (const row of rows) {
        if (row.checked && !isBlocked(row)) payload[row.field] = row.value.trim();
      }
      if (Object.keys(payload).length === 0) return;
      await contactsApi.updateContact(requestedContactId, payload);
      if (!isCurrent(requestedContactId, version)) return;
      setResult(null);
      onUpdated?.();
    } catch (error: unknown) {
      if (isCurrent(requestedContactId, version)) toast.error(saveErrorMessage(error, t));
    } finally {
      if (isCurrent(requestedContactId, version)) setSaving(false);
    }
  };

  if (!aiEnabled) return null;

  const visibleResult = result?.contactId === contactId ? result : null;
  const canFill = !!visibleResult?.rows.some((row) => row.checked && !isBlocked(row));

  return (
    <section className="border-t border-dark-border pt-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <p className="text-sm font-medium text-text-primary">{t('aiExtractTitle')}</p>
        <button type="button" className="btn-secondary text-xs" disabled={generating || saving} onClick={generate}>
          {generating ? t('aiExtractLoading') : visibleResult ? t('aiExtractRegenerate') : t('aiExtractGenerate')}
        </button>
      </div>

      {visibleResult && (
        <div className="rounded-lg border border-dark-border bg-dark-hover p-3 text-sm text-text-primary space-y-3">
          <p className="text-xs font-medium text-text-secondary">{t('aiGeneratedUnverified')}</p>
          {visibleResult.truncated && <p className="text-xs text-text-muted">{t('aiExtractTruncated')}</p>}
          {visibleResult.rows.length === 0 ? (
            <p className="text-text-secondary">{t('aiExtractEmpty')}</p>
          ) : (
            <>
              {visibleResult.rows.map((row, index) => {
                const blocked = isBlocked(row);
                return (
                  <div key={row.field} className={row.hasValue ? 'opacity-50 space-y-1' : 'space-y-1'}>
                    <label className="flex items-center gap-2 text-xs text-text-secondary">
                      <input
                        type="checkbox"
                        checked={row.checked && !blocked}
                        disabled={blocked || saving}
                        onChange={(e) => updateRow(index, { checked: e.target.checked })}
                      />
                      <span className="font-medium">{t(`common:${row.field}`)}</span>
                      {row.hasValue && <span className="text-amber-400">{t('aiExtractHasValue')}</span>}
                      {!row.hasValue && invalidEmail(row) && row.value.trim() && (
                        <span className="text-red-400">{t('common:invalidEmail')}</span>
                      )}
                    </label>
                    {row.field === 'industry' ? (
                      <select
                        className="input text-sm"
                        value={row.value}
                        disabled={row.hasValue || saving}
                        onChange={(e) => updateRow(index, { value: e.target.value })}
                      >
                        <option value="">--</option>
                        {INDUSTRIES.map((v) => <option key={v} value={v}>{t(`industries.${v}`, v)}</option>)}
                      </select>
                    ) : (
                      <input
                        className="input text-sm"
                        value={row.value}
                        disabled={row.hasValue || saving}
                        onChange={(e) => updateRow(index, { value: e.target.value })}
                      />
                    )}
                    <p className="text-xs text-text-muted break-words">{t('aiExtractEvidence', { evidence: row.evidence })}</p>
                  </div>
                );
              })}
              <div className="flex justify-end">
                <button type="button" className="btn-primary text-xs py-1 px-3" disabled={!canFill || saving} onClick={fill}>
                  {saving ? t('aiExtractFilling') : t('aiExtractFill')}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
