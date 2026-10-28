import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { aiApi } from '@/services/ai';
import type { AiProjectReport, AiProjectReportType, ApiError } from '@/types';

interface Props {
  projectId: string;
  currentStep: number;
}

const NOTES_MAX = 2000;

// docs/project-ai-report.md 4.5: each report type opens at a step; options are
// listed in step order so the last available one is the default.
const REPORT_TYPES: { type: AiProjectReportType; minStep: number }[] = [
  { type: 'inspection', minStep: 3 },
  { type: 'completion', minStep: 10 },
];

function defaultReportType(currentStep: number): AiProjectReportType | null {
  const available = REPORT_TYPES.filter((option) => currentStep >= option.minStep);
  return available.length > 0 ? available[available.length - 1].type : null;
}

function outputLanguage(language: string): 'zh-CN' | 'en' | undefined {
  if (language.startsWith('zh')) return 'zh-CN';
  if (language.startsWith('en')) return 'en';
  return undefined;
}

function errorMessage(error: unknown, t: TFunction<'projects'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('ai_report.errors.unavailable');
  switch (error.response.data?.error?.code) {
    case 'NOT_FOUND': return t('ai_report.errors.not_found');
    case 'STEP_NOT_REACHED': return t('ai_report.errors.step_not_reached');
    case 'AI_NOT_CONFIGURED': return t('ai_report.errors.not_enabled');
    case 'AI_UNAVAILABLE': return t('ai_report.errors.unavailable');
    default: return t('ai_report.errors.failed');
  }
}

export default function ProjectAiReport({ projectId, currentStep }: Props) {
  const { t, i18n } = useTranslation('projects');
  const [aiEnabled, setAiEnabled] = useState(false);
  const [reportType, setReportType] = useState<AiProjectReportType | null>(() => defaultReportType(currentStep));
  const [notes, setNotes] = useState('');
  const [generating, setGenerating] = useState(false);
  const [report, setReport] = useState<AiProjectReport | null>(null);
  const [body, setBody] = useState('');
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
    setReport(null);
    setBody('');
    setNotes('');
    return () => { requestVersion.current += 1; };
  }, [projectId]);

  useEffect(() => {
    setReportType(defaultReportType(currentStep));
  }, [projectId, currentStep]);

  const generate = async () => {
    if (generating || !reportType) return;
    const option = REPORT_TYPES.find((item) => item.type === reportType);
    if (!option || currentStep < option.minStep) return;
    const version = ++requestVersion.current;
    setGenerating(true);
    try {
      const language = outputLanguage(i18n.language);
      const response = await aiApi.createProjectReport(projectId, {
        report_type: reportType,
        notes: notes.trim() || null,
        utc_offset_minutes: 0 - new Date().getTimezoneOffset(),
        ...(language ? { output_language: language } : {}),
      });
      if (requestVersion.current === version) {
        setReport(response.data.data);
        setBody(response.data.data.body);
      }
    } catch (error: unknown) {
      if (requestVersion.current === version) toast.error(errorMessage(error, t));
    } finally {
      if (requestVersion.current === version) setGenerating(false);
    }
  };

  const copy = async () => {
    if (!report) return;
    try {
      if (!navigator.clipboard) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(`${report.title}\n\n${body}`);
      toast.success(t('ai_report.copied'));
    } catch {
      toast.error(t('ai_report.copy_failed'));
    }
  };

  if (!aiEnabled) return null;

  const anyAvailable = defaultReportType(currentStep) !== null;

  return (
    <section className="mt-4 border-t border-dark-border pt-4 space-y-3">
      <p className="text-xs font-medium text-text-muted">{t('ai_report.title')}</p>

      {!anyAvailable ? (
        <p className="text-xs text-text-muted">{t('ai_report.none_available')}</p>
      ) : (
        <>
          <div className="space-y-1.5">
            <p className="text-xs text-text-secondary">{t('ai_report.type_label')}</p>
            {REPORT_TYPES.map((option) => {
              const available = currentStep >= option.minStep;
              return (
                <label
                  key={option.type}
                  className={`flex items-center gap-2 text-sm ${available ? 'text-text-primary cursor-pointer' : 'text-text-muted opacity-50 cursor-not-allowed'}`}
                >
                  <input
                    type="radio"
                    name={`ai-report-type-${projectId}`}
                    value={option.type}
                    checked={reportType === option.type}
                    disabled={!available}
                    onChange={() => setReportType(option.type)}
                  />
                  <span>{t(`ai_report.types.${option.type}`)}</span>
                  {!available && (
                    <span className="text-[11px]">{t('ai_report.available_after', { step: option.minStep })}</span>
                  )}
                </label>
              );
            })}
          </div>

          <div className="space-y-1.5">
            <p className="text-xs text-text-secondary">{t('ai_report.notes_label')}</p>
            <textarea
              className="input text-sm w-full"
              rows={3}
              maxLength={NOTES_MAX}
              value={notes}
              placeholder={t('ai_report.notes_placeholder')}
              onChange={(event) => setNotes(event.target.value)}
            />
          </div>

          <button type="button" className="btn-secondary text-xs px-3 py-1" disabled={generating || !reportType} onClick={generate}>
            {generating ? t('ai_report.loading') : t('ai_report.generate')}
          </button>
        </>
      )}

      {anyAvailable && report && (
        <div className="rounded-lg border border-dark-border bg-dark-hover p-3 text-sm text-text-primary space-y-3">
          <p className="text-xs font-medium text-text-secondary">{t('ai_report.unverified')}</p>
          <p className="font-medium">{report.title}</p>
          <textarea
            className="input text-sm w-full"
            rows={10}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
          {report.note_for_agent.trim() && (
            <p className="text-xs text-text-muted">
              <span className="font-medium">{t('ai_report.note_for_agent')}: </span>
              {report.note_for_agent}
            </p>
          )}
          <button type="button" className="btn-primary text-xs px-3 py-1" onClick={copy}>
            {t('ai_report.copy')}
          </button>
        </div>
      )}
    </section>
  );
}
