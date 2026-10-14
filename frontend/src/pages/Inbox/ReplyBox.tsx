import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { aiApi } from '@/services/ai';
import { messagesApi } from '@/services/messages';
import type { AiLanguage, ApiError } from '@/types';

interface Props {
  contactId: string;
  channel: string;
  waReplyWindowUntil: string | null | undefined;
  contactPhone: string | null | undefined;
  aiEnabled: boolean;
  messageCount: number;
  onSent: () => void;
}

function whatsappErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('sendUnknownResult');
  const apiError = error.response.data?.error;
  if (!apiError) return t('sendFailed');
  const fields = apiError.fields || {};
  switch (apiError.code) {
    case 'WINDOW_CLOSED': return t('windowClosedAt', { closedAt: fields.window_closed_at || t('unknownTime') });
    case 'SEND_FAILED': return fields.error_code ? t('metaRejected', { code: fields.error_code, title: fields.error_title || '' }) : t('sendNotSent');
    case 'NOT_CONFIGURED': return t('chatbotNotConfigured');
    case 'NOT_ASSIGNED': return t('notAssigned');
    case 'NO_PHONE': return t('noPhone');
    case 'RETRY_EXPIRED': return t('retryExpired');
    case 'IN_PROGRESS': return t('sendingInProgress');
    default: return t('sendFailed');
  }
}

function aiErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('aiTemporarilyUnavailable');
  switch (error.response.data?.error?.code) {
    case 'AI_UNAVAILABLE': return t('aiTemporarilyUnavailable');
    case 'AI_ERROR': return t('aiError');
    case 'AI_NOT_CONFIGURED': return t('aiNotEnabled');
    case 'NO_MESSAGES': return t('aiNoMessages');
    case 'NOT_ASSIGNED': return t('notAssigned');
    default: return t('aiRequestFailed');
  }
}

function defaultDraftLanguage(language: string): AiLanguage {
  return language.startsWith('en') ? 'en' : 'zh-CN';
}

export default function ReplyBox({ contactId, channel, waReplyWindowUntil, contactPhone, aiEnabled, messageCount, onSent }: Props) {
  const { t, i18n } = useTranslation('inbox');
  const [message, setMessage] = useState('');
  const [subject, setSubject] = useState('');
  const [instruction, setInstruction] = useState('');
  const [outputLanguage, setOutputLanguage] = useState<AiLanguage>(() => defaultDraftLanguage(i18n.language));
  const [translationLanguage, setTranslationLanguage] = useState<AiLanguage>('en');
  const [noteForAgent, setNoteForAgent] = useState('');
  const [sending, setSending] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [now, setNow] = useState(Date.now());
  const activeContactId = useRef(contactId);
  const previousContactId = useRef(contactId);
  const aiRequestVersion = useRef(0);
  if (previousContactId.current !== contactId) {
    previousContactId.current = contactId;
    aiRequestVersion.current += 1;
  }
  activeContactId.current = contactId;
  const isWhatsApp = channel === 'whatsapp';
  const windowOpen = !isWhatsApp || waReplyWindowUntil === undefined || (!!waReplyWindowUntil && new Date(waReplyWindowUntil).getTime() > now);
  const missingPhone = isWhatsApp && contactPhone !== undefined && !contactPhone?.trim();
  const replyDisabled = !windowOpen || missingPhone;
  const normalizedMessage = message.trim();
  const translationInvalid = !normalizedMessage || Array.from(normalizedMessage).length > 2000;

  useEffect(() => {
    if (!isWhatsApp || !waReplyWindowUntil) return undefined;
    const delay = new Date(waReplyWindowUntil).getTime() - Date.now();
    if (delay <= 0) {
      setNow(Date.now());
      return undefined;
    }
    const timer = window.setTimeout(() => setNow(Date.now()), delay);
    return () => window.clearTimeout(timer);
  }, [isWhatsApp, waReplyWindowUntil]);

  useEffect(() => {
    setMessage('');
    setSubject('');
    setInstruction('');
    setNoteForAgent('');
    setDrafting(false);
    setTranslating(false);
  }, [contactId]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!normalizedMessage || sending || replyDisabled) return;
    setSending(true);
    try {
      const response = channel === 'email' ? await messagesApi.sendEmail(contactId, subject, message) : await messagesApi.sendWhatsApp(contactId, message);
      setMessage('');
      setSubject('');
      if (response.status === 200 || response.status === 202) setNoteForAgent('');
      onSent();
      if (isWhatsApp && response.status === 202) toast(t('sentUnconfirmed'));
    } catch (error: unknown) {
      if (channel === 'email') {
        const apiMessage = axios.isAxiosError<ApiError>(error) ? error.response?.data?.error?.message : undefined;
        toast.error(apiMessage || t('sendFailed'));
      } else if (!axios.isAxiosError(error) || !error.response) {
        onSent();
        toast.error(t('sendUnknownResult'));
      } else {
        toast.error(whatsappErrorMessage(error, t));
      }
    } finally {
      setSending(false);
    }
  };

  const createDraft = async () => {
    if (drafting || replyDisabled || messageCount === 0) return;
    if ((message.trim() || subject.trim()) && !window.confirm(t('aiOverwriteConfirm'))) return;
    const requestedContactId = contactId;
    const requestVersion = ++aiRequestVersion.current;
    setNoteForAgent('');
    setDrafting(true);
    try {
      const response = await aiApi.createReplyDraft(requestedContactId, isWhatsApp ? 'whatsapp' : 'email', instruction.trim() || undefined, outputLanguage);
      if (activeContactId.current !== requestedContactId || aiRequestVersion.current !== requestVersion) return;
      setMessage(response.data.data.draft);
      if (!isWhatsApp) setSubject(response.data.data.subject || '');
      setNoteForAgent(response.data.data.note_for_agent);
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId && aiRequestVersion.current === requestVersion) toast.error(aiErrorMessage(error, t));
    } finally {
      if (activeContactId.current === requestedContactId && aiRequestVersion.current === requestVersion) setDrafting(false);
    }
  };

  const translateReply = async () => {
    if (translating || replyDisabled || translationInvalid) return;
    if (!window.confirm(t('aiOverwriteConfirm'))) return;
    const requestedContactId = contactId;
    const requestVersion = ++aiRequestVersion.current;
    setTranslating(true);
    try {
      const response = await aiApi.translate(requestedContactId, normalizedMessage, translationLanguage);
      if (activeContactId.current !== requestedContactId || aiRequestVersion.current !== requestVersion) return;
      const translation = response.data.data.translation;
      if (isWhatsApp && Array.from(translation).length > 4096) {
        toast.error(t('aiTranslationTooLong'));
        return;
      }
      setMessage(translation);
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId && aiRequestVersion.current === requestVersion) toast.error(aiErrorMessage(error, t));
    } finally {
      if (activeContactId.current === requestedContactId && aiRequestVersion.current === requestVersion) setTranslating(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-2 border-t border-dark-border p-3">
      {channel === 'email' && <input className="input text-sm" placeholder={t('subject')} value={subject} disabled={replyDisabled} onChange={(event) => setSubject(event.target.value)} />}
      {isWhatsApp && !windowOpen && <p className="text-xs text-text-muted">{t('windowClosed')}</p>}
      {missingPhone && <p className="text-xs text-text-muted">{t('replyNoPhone')}</p>}
      {aiEnabled && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-secondary text-xs" disabled={drafting || translating || replyDisabled || messageCount === 0} onClick={createDraft}>{drafting ? t('aiDraftLoading') : t('aiDraft')}</button>
            <input className="input min-w-40 flex-1 text-xs" maxLength={200} disabled={replyDisabled || drafting || translating} placeholder={t('aiDraftInstruction')} value={instruction} onChange={(event) => setInstruction(event.target.value)} />
            <select className="input w-auto text-xs" disabled={replyDisabled || drafting || translating} value={outputLanguage} onChange={(event) => setOutputLanguage(event.target.value as AiLanguage)}>
              <option value="zh-CN">{t('aiLanguageZhCn')}</option><option value="en">{t('aiLanguageEn')}</option><option value="ms">{t('aiLanguageMs')}</option>
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-secondary text-xs" disabled={drafting || translating || replyDisabled || translationInvalid} onClick={translateReply}>{translating ? t('aiTranslationLoading') : t('aiTranslateReply')}</button>
            <select className="input w-auto text-xs" disabled={replyDisabled || drafting || translating} value={translationLanguage} onChange={(event) => setTranslationLanguage(event.target.value as AiLanguage)}>
              <option value="zh-CN">{t('aiLanguageZhCn')}</option><option value="en">{t('aiLanguageEn')}</option><option value="ms">{t('aiLanguageMs')}</option>
            </select>
            {translationInvalid && <p className="text-xs text-text-muted">{!normalizedMessage ? t('aiTranslationEmpty') : t('aiTranslationInputTooLong')}</p>}
          </div>
          {noteForAgent && <p className="text-xs text-text-secondary">{t('aiGeneratedUnverified')} · {noteForAgent}</p>}
        </div>
      )}
      <div className="flex gap-2">
        <textarea className="input flex-1 resize-none text-sm" placeholder={t('replyPlaceholder')} value={message} maxLength={isWhatsApp ? 4096 : undefined} rows={3} disabled={replyDisabled} onChange={(event) => setMessage(event.target.value)} />
        <button type="submit" disabled={sending || !normalizedMessage || replyDisabled} className="btn-primary px-4 text-sm">{t('send')}</button>
      </div>
    </form>
  );
}
