import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { aiApi } from '@/services/ai';
import type { AiDealNextStep, ApiError, DealStatus } from '@/types';

interface Props {
  dealId: string;
  status: DealStatus;
}

function outputLanguage(language: string): 'zh-CN' | 'en' | undefined {
  if (language.startsWith('zh')) return 'zh-CN';
  if (language.startsWith('en')) return 'en';
  return undefined;
}

function errorMessage(error: unknown, t: TFunction<'common'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('dealAiTemporarilyUnavailable');
  switch (error.response.data?.error?.code) {
    case 'NOT_ASSIGNED': return t('dealAiNotAssigned');
    case 'AI_NOT_CONFIGURED': return t('dealAiNotEnabled');
    case 'DEAL_CLOSED': return t('dealAiClosed');
    case 'NO_HISTORY': return t('dealAiNoHistory');
    case 'AI_UNAVAILABLE': return t('dealAiTemporarilyUnavailable');
    case 'AI_ERROR': return t('dealAiRequestFailed');
    default: return t('dealAiRequestFailed');
  }
}

export default function DealAiNextStep({ dealId, status }: Props) {
  const { t, i18n } = useTranslation('common');
  const [aiEnabled, setAiEnabled] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [suggestion, setSuggestion] = useState<AiDealNextStep | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => {
    let active = true;
    void aiApi.getStatus().then(
      (aiStatus) => { if (active) setAiEnabled(aiStatus.enabled); },
      () => { if (active) setAiEnabled(false); },
    );
    return () => { active = false; };
  }, []);

  useEffect(() => {
    requestVersion.current += 1;
    setGenerating(false);
    setSuggestion(null);
    return () => { requestVersion.current += 1; };
  }, [dealId]);

  const generate = async () => {
    if (generating) return;
    const requestedDealId = dealId;
    const version = ++requestVersion.current;
    setGenerating(true);
    try {
      const response = await aiApi.createDealNextStep(requestedDealId, outputLanguage(i18n.language));
      if (dealId === requestedDealId && requestVersion.current === version) {
        setSuggestion(response.data.data);
      }
    } catch (error: unknown) {
      if (dealId === requestedDealId && requestVersion.current === version) {
        toast.error(errorMessage(error, t));
      }
    } finally {
      if (dealId === requestedDealId && requestVersion.current === version) {
        setGenerating(false);
      }
    }
  };

  if (!aiEnabled || status === 'won' || status === 'lost') return null;

  return (
    <section className="mx-4 mb-4 border-t border-dark-border pt-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-text-primary">{t('dealAiNextStep')}</p>
          <p className="text-xs text-text-muted">{t('dealAiSavedContent')}</p>
        </div>
        <button type="button" className="btn-secondary text-xs" disabled={generating} onClick={generate}>
          {generating ? t('dealAiLoading') : suggestion ? t('dealAiRegenerate') : t('dealAiGenerate')}
        </button>
      </div>

      {suggestion && (
        <div className="rounded-lg border border-dark-border bg-dark-hover p-3 text-sm text-text-primary space-y-3">
          <div className={`flex flex-wrap items-center gap-2 text-xs font-medium ${suggestion.stalled ? 'text-amber-400' : 'text-text-secondary'}`}>
            <span>{t('dealAiFacts', { idleDays: suggestion.idle_days, daysInStatus: suggestion.days_in_status })}</span>
            {suggestion.stalled && <span className="rounded-full bg-amber-400/15 px-2 py-0.5">{t('dealAiStalled')}</span>}
          </div>
          <p className="text-xs font-medium text-text-secondary">{t('dealAiGeneratedUnverified')}</p>
          {suggestion.truncated && <p className="text-xs text-text-muted">{t('dealAiTruncated')}</p>}
          <div>
            <p className="font-medium">{t('dealAiAssessment')}</p>
            <p className="mt-1 whitespace-pre-wrap text-text-secondary">{suggestion.assessment}</p>
          </div>
          {suggestion.stall_reason && (
            <div>
              <p className="font-medium">{t('dealAiStallReason')}</p>
              <p className="mt-1 whitespace-pre-wrap text-text-secondary">{suggestion.stall_reason}</p>
            </div>
          )}
          <div>
            <p className="font-medium">{t('dealAiNextSteps')}</p>
            <ol className="mt-1 list-decimal space-y-1 pl-5 text-text-secondary">
              {suggestion.next_steps.map((step, index) => <li key={index}>{step}</li>)}
            </ol>
          </div>
        </div>
      )}
    </section>
  );
}
