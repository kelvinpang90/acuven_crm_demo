import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { aiApi } from '@/services/ai';
import type { AiContactSummary, ApiError } from '@/types';

interface Props {
  contactId: string;
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
    case 'NO_HISTORY': return t('aiNoHistory');
    case 'AI_UNAVAILABLE': return t('aiTemporarilyUnavailable');
    case 'AI_ERROR': return t('aiError');
    default: return t('aiRequestFailed');
  }
}

export default function ContactAiSummary({ contactId }: Props) {
  const { t, i18n } = useTranslation('contacts');
  const [aiEnabled, setAiEnabled] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [summary, setSummary] = useState<{ contactId: string; data: AiContactSummary } | null>(null);
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
    setSummary(null);
    return () => { requestVersion.current += 1; };
  }, [contactId]);

  const generate = async () => {
    if (generating) return;
    const requestedContactId = contactId;
    const version = ++requestVersion.current;
    setGenerating(true);
    try {
      const response = await aiApi.createContactSummary(requestedContactId, outputLanguage(i18n.language));
      if (activeContactId.current === requestedContactId && requestVersion.current === version) {
        setSummary({ contactId: requestedContactId, data: response.data.data });
      }
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId && requestVersion.current === version) {
        toast.error(errorMessage(error, t));
      }
    } finally {
      if (activeContactId.current === requestedContactId && requestVersion.current === version) {
        setGenerating(false);
      }
    }
  };

  if (!aiEnabled) return null;

  const visibleSummary = summary?.contactId === contactId ? summary.data : null;

  return (
    <section className="border-t border-dark-border pt-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <p className="text-sm font-medium text-text-primary">{t('aiCustomerSummary')}</p>
        <button type="button" className="btn-secondary text-xs" disabled={generating} onClick={generate}>
          {generating ? t('aiSummaryLoading') : visibleSummary ? t('aiSummaryRegenerate') : t('aiSummaryGenerate')}
        </button>
      </div>

      {visibleSummary && (
        <div className="rounded-lg border border-dark-border bg-dark-hover p-3 text-sm text-text-primary space-y-2">
          <p className="text-xs font-medium text-text-secondary">{t('aiGeneratedUnverified')}</p>
          {visibleSummary.truncated && <p className="text-xs text-text-muted">{t('aiSummaryTruncated')}</p>}
          <p className="whitespace-pre-wrap">{visibleSummary.summary}</p>
          {visibleSummary.suggested_next_step && (
            <p className="text-text-secondary">
              <span className="font-medium text-text-primary">{t('aiSuggestedNextStep')}: </span>
              {visibleSummary.suggested_next_step}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
