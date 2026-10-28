import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';
import { projectsApi } from '@/services/projects';
import { PROJECT_STEPS, TOTAL_STEPS, stepName, getAlertLevel } from './steps';
import WarrantyConfirmModal from './WarrantyConfirmModal';
import ProjectAiReport from './ProjectAiReport';
import type { Project, ProjectAlertLevel, ProjectStepHistory } from '@/types';

const BADGE: Record<ProjectAlertLevel, string> = {
  normal: 'bg-status-following-bg text-status-following',
  watch: 'bg-status-negotiating-bg text-status-negotiating',
  urgent: 'bg-status-lost-bg text-status-lost',
  done: 'bg-dark-hover text-text-muted',
};

interface Props {
  project: Project;
  onClose: () => void;
  onChanged: (updated: Project) => void;
  onEdit: (project: Project) => void;
  onDelete: (project: Project) => void;
}

export default function ProjectDetail({ project, onClose, onChanged, onEdit, onDelete }: Props) {
  const { t } = useTranslation('projects');
  const [advancing, setAdvancing] = useState(false);
  const [showWarrantyConfirm, setShowWarrantyConfirm] = useState(false);

  const level = getAlertLevel(project);
  const atFinal = project.current_step >= TOTAL_STEPS;
  const entersWarranty = project.current_step + 1 === TOTAL_STEPS;

  const historyByStep = new Map<number, ProjectStepHistory>();
  for (const h of project.history) historyByStep.set(h.step_no, h);

  const handleAdvance = async () => {
    if (atFinal) return;
    // Entering step 12 (warranty_active) is gated behind the satisfaction
    // score + signature form instead of advancing directly.
    if (entersWarranty) {
      setShowWarrantyConfirm(true);
      return;
    }
    setAdvancing(true);
    try {
      const updated = await projectsApi.advanceStep(project.id);
      onChanged(updated);
    } catch {
      /* ignore */
    }
    setAdvancing(false);
  };

  const handleWarrantySubmit = async (payload: {
    satisfaction_score: number;
    customer_feedback: string;
    signature_data: string;
  }) => {
    const updated = await projectsApi.advanceStep(project.id, null, payload);
    onChanged(updated);
    setShowWarrantyConfirm(false);
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-50" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="bg-dark-card border border-dark-border rounded-xl w-full max-w-2xl max-h-[90vh] flex flex-col pointer-events-auto shadow-xl">
          {/* Header */}
          <div className="flex items-start justify-between gap-3 p-4 border-b border-dark-border">
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-base font-semibold text-text-primary truncate">{project.customer_name}</h2>
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${BADGE[level]}`}>
                  {t(`alert.${level}`)}
                </span>
              </div>
              <p className="text-xs text-text-muted mt-0.5 truncate">
                {[project.address, project.service_type, project.project_manager].filter(Boolean).join(' · ')}
              </p>
            </div>
            <button
              onClick={onClose}
              className="p-1 rounded hover:bg-dark-hover text-text-muted hover:text-text-primary transition-colors shrink-0"
              aria-label={t('actions.close')}
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          {/* Action bar */}
          <div className="flex items-center gap-2 p-3 border-b border-dark-border">
            <button
              onClick={handleAdvance}
              disabled={atFinal || advancing}
              className="px-3 py-1.5 text-sm bg-primary text-white rounded-lg hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {atFinal ? t('advance_done') : t('advance')}
            </button>
            <div className="flex-1" />
            <button
              onClick={() => onEdit(project)}
              className="px-3 py-1.5 text-sm text-text-secondary border border-dark-border rounded-lg hover:bg-dark-hover transition-colors"
            >
              {t('actions.edit')}
            </button>
            <button
              onClick={() => onDelete(project)}
              className="px-3 py-1.5 text-sm text-status-lost border border-dark-border rounded-lg hover:bg-status-lost/10 transition-colors"
            >
              {t('actions.delete')}
            </button>
          </div>

          {/* Timeline */}
          <div className="p-4 overflow-y-auto">
            <p className="text-xs font-medium text-text-muted mb-3">{t('timeline_title')}</p>
            <ol className="space-y-0">
              {PROJECT_STEPS.map((s) => {
                const completed = s.no < project.current_step;
                const current = s.no === project.current_step;
                const hist = historyByStep.get(s.no);
                const isLast = s.no === TOTAL_STEPS;

                const dot = completed
                  ? 'bg-status-following border-status-following text-white'
                  : current
                  ? 'bg-primary border-primary text-white'
                  : 'bg-dark-card border-dark-border text-text-muted';

                return (
                  <li key={s.no} className="flex gap-3">
                    {/* Marker + connector */}
                    <div className="flex flex-col items-center">
                      <div className={`w-6 h-6 rounded-full border flex items-center justify-center text-[11px] font-medium shrink-0 ${dot}`}>
                        {completed ? (
                          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        ) : (
                          s.no
                        )}
                      </div>
                      {!isLast && <div className={`w-px flex-1 min-h-[24px] ${completed ? 'bg-status-following' : 'bg-dark-border'}`} />}
                    </div>

                    {/* Content */}
                    <div className={`min-w-0 flex-1 ${isLast ? 'pb-1' : 'pb-4'}`}>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-sm ${current ? 'font-semibold text-text-primary' : completed ? 'text-text-primary' : 'text-text-muted'}`}>
                          {stepName(s.no, t)}
                        </span>
                        {current && (
                          <span className="text-[11px] text-primary">{t('state.current')}</span>
                        )}
                        {s.optional && !hist && (
                          <span className="text-[11px] text-text-muted">({t('state.optional')})</span>
                        )}
                      </div>

                      {hist && (
                        <div className="mt-1 text-xs text-text-muted">
                          {dayjs(hist.entered_at).format('YYYY-MM-DD')} · {t('updated_by')} {hist.updated_by}
                          {hist.note ? ` · ${hist.note}` : ''}
                        </div>
                      )}

                      {hist && hist.photos.length > 0 && (
                        <div className="mt-2">
                          <p className="text-[11px] text-text-muted mb-1">{t('photos_title')}</p>
                          <div className="flex gap-2 flex-wrap">
                          {hist.photos.map((name) => (
                            <div
                              key={name}
                              title={name}
                              className="w-16 h-16 rounded-lg bg-dark-bg border border-dark-border flex flex-col items-center justify-center gap-1 text-text-muted"
                            >
                              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                <rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
                              </svg>
                              <span className="text-[9px] px-1 truncate max-w-[60px]">{name}</span>
                            </div>
                          ))}
                          </div>
                        </div>
                      )}

                      {isLast && project.satisfaction_score != null && (
                        <div className="mt-2 space-y-1.5">
                          <p className="text-xs text-text-muted">
                            {t('warranty_record.score_label')}: {project.satisfaction_score}/10
                          </p>
                          {project.customer_feedback && (
                            <p className="text-xs text-text-muted">
                              {t('warranty_record.feedback_label')}: {project.customer_feedback}
                            </p>
                          )}
                          {project.signature_data && (
                            <div>
                              <p className="text-[11px] text-text-muted mb-1">{t('warranty_record.signature_label')}</p>
                              <img
                                src={project.signature_data}
                                alt={t('warranty_record.signature_label')}
                                className="h-16 bg-dark-bg border border-dark-border rounded-lg"
                              />
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>

            <ProjectAiReport projectId={project.id} currentStep={project.current_step} />
          </div>
        </div>
      </div>

      <WarrantyConfirmModal
        open={showWarrantyConfirm}
        customerName={project.customer_name}
        onClose={() => setShowWarrantyConfirm(false)}
        onSubmit={handleWarrantySubmit}
      />
    </>
  );
}
