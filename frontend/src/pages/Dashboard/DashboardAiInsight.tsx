import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { aiApi } from '@/services/ai';
import type { AiDashboardInsight, ApiError } from '@/types';

function outputLanguage(language: string): 'zh-CN' | 'en' | undefined {
  if (language.startsWith('zh')) return 'zh-CN';
  if (language.startsWith('en')) return 'en';
  return undefined;
}

function errorMessage(error: unknown, t: TFunction<'dashboard'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('aiTemporarilyUnavailable');
  switch (error.response.data?.error?.code) {
    case 'AI_NOT_CONFIGURED': return t('aiNotEnabled');
    case 'NO_DATA': return t('aiInsightNoData');
    case 'AI_UNAVAILABLE': return t('aiTemporarilyUnavailable');
    default: return t('aiRequestFailed');
  }
}

export default function DashboardAiInsight() {
  const { t, i18n } = useTranslation('dashboard');
  const [aiEnabled, setAiEnabled] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [insight, setInsight] = useState<AiDashboardInsight | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => {
    let active = true;
    void aiApi.getStatus().then(
      (status) => { if (active) setAiEnabled(status.enabled); },
      () => { if (active) setAiEnabled(false); },
    );
    return () => { active = false; };
  }, []);

  useEffect(() => () => { requestVersion.current += 1; }, []);

  const generate = async () => {
    if (generating) return;
    const version = ++requestVersion.current;
    setGenerating(true);
    try {
      const response = await aiApi.createDashboardInsight(outputLanguage(i18n.language));
      if (requestVersion.current === version) setInsight(response.data.data);
    } catch (error: unknown) {
      if (requestVersion.current === version) toast.error(errorMessage(error, t));
    } finally {
      if (requestVersion.current === version) setGenerating(false);
    }
  };

  if (!aiEnabled) return null;

  return (
    <section className="bg-dark-card border border-dark-border rounded-xl p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-text-primary">{t('aiInsightTitle')}</p>
          <p className="text-xs text-text-muted">{t('aiInsightScope')}</p>
        </div>
        {!insight && (
          <button type="button" className="btn-secondary text-xs" disabled={generating} onClick={generate}>
            {generating ? t('aiInsightLoading') : t('aiInsightGenerate')}
          </button>
        )}
      </div>

      {insight && (
        <div className="mt-3 rounded-lg border border-dark-border bg-dark-hover p-3 text-sm text-text-primary space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-medium text-text-secondary">{t('aiGeneratedUnverified')}</p>
            <button type="button" className="btn-secondary text-xs" disabled={generating} onClick={generate}>
              {generating ? t('aiInsightLoading') : t('aiInsightRegenerate')}
            </button>
          </div>
          {insight.truncated && <p className="text-xs text-text-muted">{t('aiInsightTruncated')}</p>}
          <p className="font-medium whitespace-pre-wrap">{insight.headline}</p>
          {insight.observations.length > 0 && (
            <div>
              <p className="text-xs font-medium text-text-secondary mb-1">{t('aiInsightObservations')}</p>
              <ul className="list-disc pl-5 space-y-1">
                {insight.observations.map((item, index) => <li key={index} className="whitespace-pre-wrap">{item}</li>)}
              </ul>
            </div>
          )}
          {insight.suggestions.length > 0 && (
            <div>
              <p className="text-xs font-medium text-text-secondary mb-1">{t('aiInsightSuggestions')}</p>
              <ul className="list-disc pl-5 space-y-1">
                {insight.suggestions.map((item, index) => <li key={index} className="whitespace-pre-wrap">{item}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
