import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import dayjs from 'dayjs';
import { aiApi } from '@/services/ai';
import { tasksApi } from '@/services/tasks';
import type { ActivityType, AiFollowUpDraft, AiFollowUpFromChat, ApiError, Priority } from '@/types';

type DraftSource = 'note' | 'chat';

interface Props {
  contactId: string;
  dealId: string;
  note: string;
  activityType: Exclude<ActivityType, 'status change'>;
  onFill: (activityType: Exclude<ActivityType, 'status change'>, note: string) => void;
  onRestore: (activityType: Exclude<ActivityType, 'status change'>, note: string) => void;
  onPendingTasksChange: (count: number) => void;
}

interface EditableTask {
  title: string;
  dueDate: string;
  priority: Priority;
  added: boolean;
  adding: boolean;
  error: string | null;
}

function outputLanguage(language: string): 'zh-CN' | 'en' | undefined {
  if (language.startsWith('zh')) return 'zh-CN';
  if (language.startsWith('en')) return 'en';
  return undefined;
}

function localToday() {
  const date = new Date();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function errorMessage(error: unknown, t: TFunction<'contacts'>, source: DraftSource) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('aiTemporarilyUnavailable');
  const code = error.response.data?.error?.code;
  if (source === 'chat' && code === 'NO_MESSAGES') return t('aiFromChatNoMessages');
  switch (code) {
    case 'AI_NOT_CONFIGURED': return t('aiNotEnabled');
    case 'AI_UNAVAILABLE': return t('aiTemporarilyUnavailable');
    case 'INVALID_INPUT': return t(source === 'chat' ? 'aiFromChatInvalidInput' : 'aiFollowUpInvalidInput');
    case 'AI_ERROR': return t(source === 'chat' ? 'aiRequestFailed' : 'aiError');
    default: return t('aiRequestFailed');
  }
}

export default function FollowUpAiDraft({
  contactId,
  dealId,
  note,
  activityType,
  onFill,
  onRestore,
  onPendingTasksChange,
}: Props) {
  const { t, i18n } = useTranslation('contacts');
  const [enabled, setEnabled] = useState(false);
  const [generating, setGenerating] = useState<DraftSource | null>(null);
  const [draft, setDraft] = useState<AiFollowUpDraft | null>(null);
  const [basis, setBasis] = useState<{ since: string | null; truncated: boolean } | null>(null);
  const [tasks, setTasks] = useState<EditableTask[]>([]);
  const [restore, setRestore] = useState<{ activityType: Exclude<ActivityType, 'status change'>; note: string } | null>(null);
  const requestVersion = useRef(0);
  const cardVersion = useRef(0);

  useEffect(() => {
    let active = true;
    void aiApi.getStatus().then(
      (status) => { if (active) setEnabled(status.enabled); },
      () => { if (active) setEnabled(false); },
    );
    return () => { active = false; };
  }, []);

  useEffect(() => {
    setRestore(null);
  }, [contactId]);

  useEffect(() => {
    requestVersion.current += 1;
    cardVersion.current += 1;
    setGenerating(null);
    setDraft(null);
    setBasis(null);
    setTasks([]);
  }, [contactId, dealId]);

  useEffect(() => {
    onPendingTasksChange(tasks.filter((task) => !task.added).length);
  }, [onPendingTasksChange, tasks]);

  useEffect(() => () => {
    requestVersion.current += 1;
    cardVersion.current += 1;
  }, []);

  const showDraft = (result: AiFollowUpDraft, resultBasis: { since: string | null; truncated: boolean } | null) => {
    cardVersion.current += 1;
    setDraft(result);
    setBasis(resultBasis);
    setTasks(result.tasks.map((task) => ({
      title: task.title,
      dueDate: task.due_date || '',
      priority: task.priority,
      added: false,
      adding: false,
      error: null,
    })));
  };

  const generate = async () => {
    if (generating || !note.trim() || Array.from(note).length > 2000 || !dealId) return;
    const version = ++requestVersion.current;
    setGenerating('note');
    try {
      const response = await aiApi.createFollowUpDraft(contactId, {
        deal_id: dealId,
        note,
        today: localToday(),
        ...(outputLanguage(i18n.language) ? { output_language: outputLanguage(i18n.language) } : {}),
      });
      if (requestVersion.current === version) showDraft(response.data.data, null);
    } catch (error: unknown) {
      if (requestVersion.current === version) toast.error(errorMessage(error, t, 'note'));
    } finally {
      if (requestVersion.current === version) setGenerating(null);
    }
  };

  const generateFromChat = async () => {
    if (generating || !dealId) return;
    const version = ++requestVersion.current;
    setGenerating('chat');
    try {
      const response = await aiApi.createFollowUpFromChat(contactId, {
        deal_id: dealId,
        today: localToday(),
        utc_offset_minutes: 0 - new Date().getTimezoneOffset(),
        ...(outputLanguage(i18n.language) ? { output_language: outputLanguage(i18n.language) } : {}),
      });
      if (requestVersion.current === version) {
        const result: AiFollowUpFromChat = response.data.data;
        showDraft(result, { since: result.since, truncated: result.truncated });
      }
    } catch (error: unknown) {
      if (requestVersion.current === version) toast.error(errorMessage(error, t, 'chat'));
    } finally {
      if (requestVersion.current === version) setGenerating(null);
    }
  };

  const fill = () => {
    if (!draft) return;
    setRestore({ activityType, note });
    onFill(draft.activity_type, draft.content);
  };

  const restoreNote = () => {
    if (!restore) return;
    onRestore(restore.activityType, restore.note);
    setRestore(null);
  };

  const updateTask = (index: number, update: Partial<EditableTask>) => {
    setTasks((current) => current.map((task, taskIndex) => taskIndex === index ? { ...task, ...update } : task));
  };

  const addTask = async (index: number) => {
    const task = tasks[index];
    if (!task || task.adding || task.added || !task.title.trim()) return;
    const version = cardVersion.current;
    updateTask(index, { adding: true, error: null });
    try {
      await tasksApi.createTask({
        title: task.title.trim(),
        contact_id: contactId,
        due_date: task.dueDate || null,
        priority: task.priority,
      });
      if (cardVersion.current === version) updateTask(index, { adding: false, added: true });
    } catch (error: unknown) {
      const message = axios.isAxiosError<ApiError>(error) ? error.response?.data?.error?.message : undefined;
      if (cardVersion.current === version) updateTask(index, { adding: false, error: message || t('aiFollowUpTaskFailed') });
    }
  };

  if (!enabled) return null;

  const tooLong = Array.from(note).length > 2000;
  const disabled = !!generating || !note.trim() || tooLong || !dealId;

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button type="button" className="btn-secondary text-xs px-3 py-1" disabled={disabled} onClick={generate}>
          {generating === 'note' ? t('aiFollowUpLoading') : t('aiFollowUpGenerate')}
        </button>
        <button type="button" className="btn-secondary text-xs px-3 py-1" disabled={!!generating || !dealId} onClick={generateFromChat}>
          {generating === 'chat' ? t('aiFromChatLoading') : t('aiFromChatGenerate')}
        </button>
      </div>
      {tooLong && <p className="text-xs text-red-500">{t('aiFollowUpTooLong')}</p>}
      {draft && (
        <div className="rounded-lg border border-dark-border bg-dark-hover p-3 text-sm space-y-3">
          <p className="text-xs font-medium text-text-secondary">{t('aiGeneratedUnverified')}</p>
          {basis && (
            <p className="text-xs text-text-muted">
              {basis.since
                ? t('aiFromChatBasisSince', { time: dayjs(basis.since).format('YYYY-MM-DD HH:mm') })
                : t('aiFromChatBasisAll')}
              {basis.truncated && ` ${t('aiFromChatTruncated')}`}
            </p>
          )}
          <p><span className="font-medium">{t('followType')}: </span>{t(`followTypes.${draft.activity_type}`)}</p>
          <p className="whitespace-pre-wrap text-text-secondary">{draft.content}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-primary text-xs px-3 py-1" onClick={fill}>{t('aiFollowUpFill')}</button>
            {restore && <button type="button" className="btn-secondary text-xs px-3 py-1" onClick={restoreNote}>{t('aiFollowUpRestore')}</button>}
          </div>
          <div className="space-y-3">
            <p className="font-medium">{t('aiFollowUpTasks')}</p>
            {tasks.length === 0 && <p className="text-xs text-text-muted">{t('aiFollowUpNoTasks')}</p>}
            {tasks.map((task, index) => (
              <div key={index} className="space-y-2 border-t border-dark-border pt-2">
                <input className="input text-sm" value={task.title} onChange={(event) => updateTask(index, { title: event.target.value })} />
                <div className="flex gap-2">
                  <input type="date" className="input text-sm flex-1" value={task.dueDate} onChange={(event) => updateTask(index, { dueDate: event.target.value })} />
                  <select className="input text-sm" value={task.priority} onChange={(event) => updateTask(index, { priority: event.target.value as Priority })}>
                    <option value="high">{t('common:high')}</option>
                    <option value="mid">{t('common:medium')}</option>
                    <option value="low">{t('common:low')}</option>
                  </select>
                </div>
                {task.error && <p className="text-xs text-red-500">{task.error}</p>}
                <button type="button" className="btn-secondary text-xs px-3 py-1" disabled={task.added || task.adding || !task.title.trim()} onClick={() => addTask(index)}>
                  {task.added ? t('aiFollowUpTaskAdded') : task.adding ? t('aiFollowUpTaskAdding') : t('aiFollowUpAddTask')}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
