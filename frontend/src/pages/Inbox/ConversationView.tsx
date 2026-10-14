import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { messagesApi } from '@/services/messages';
import { contactsApi, type WhatsAppHandover } from '@/services/contacts';
import { aiApi } from '@/services/ai';
import type { AiConversationBrief, AiLanguage, ApiError, Contact, Message } from '@/types';
import dayjs from 'dayjs';
import ReplyBox from './ReplyBox';

interface Props {
  contactId: string;
  contactName: string;
  channel: string;
}

function retryErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('sendUnknownResult');
  const apiError = error.response?.data?.error;
  if (!apiError) return t('sendFailed');
  const fields = apiError.fields || {};
  switch (apiError.code) {
    case 'WINDOW_CLOSED': return t('windowClosedAt', { closedAt: fields.window_closed_at || t('unknownTime') });
    case 'SEND_FAILED': return fields.error_code
      ? t('metaRejected', { code: fields.error_code, title: fields.error_title || '' })
      : t('sendNotSent');
    case 'NOT_CONFIGURED': return t('chatbotNotConfigured');
    case 'NOT_ASSIGNED': return t('notAssigned');
    case 'NO_PHONE': return t('noPhone');
    case 'RETRY_EXPIRED': return t('retryExpired');
    case 'IN_PROGRESS': return t('sendingInProgress');
    case 'NO_IDENTITY': return t('noWhatsAppIdentity');
    default: return t('sendFailed');
  }
}

function handbackErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('handbackFailed');
  const apiError = error.response.data?.error;
  if (!apiError) return t('handbackFailed');

  switch (apiError.code) {
    case 'NOT_CONFIGURED': return t('chatbotNotConfigured');
    case 'NOT_ASSIGNED': return t('notAssigned');
    case 'NO_IDENTITY': return t('noWhatsAppIdentity');
    case 'CHATBOT_FAILED': return t('handbackFailed');
    default: return apiError.message || t('handbackFailed');
  }
}

function isHandoverActive(handover: WhatsAppHandover) {
  return handover.wa_handover_active && !!handover.wa_handover_updated_at;
}

function briefErrorMessage(error: unknown, t: TFunction<'inbox'>) {
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

function briefOutputLanguage(language: string): 'zh-CN' | 'en' | undefined {
  if (language.startsWith('zh')) return 'zh-CN';
  if (language.startsWith('en')) return 'en';
  return undefined;
}

function bubbleLanguage(language: string): AiLanguage {
  return language.startsWith('en') ? 'en' : 'zh-CN';
}

export default function ConversationView({ contactId, contactName, channel }: Props) {
  const { t, i18n } = useTranslation('inbox');
  const [messages, setMessages] = useState<Message[]>([]);
  const [contact, setContact] = useState<Contact | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [handingBack, setHandingBack] = useState(false);
  const [aiEnabled, setAiEnabled] = useState(false);
  const [briefing, setBriefing] = useState(false);
  const [brief, setBrief] = useState<{ contactId: string; data: AiConversationBrief } | null>(null);
  const [messageTranslations, setMessageTranslations] = useState<Record<string, string>>({});
  const [translatingId, setTranslatingId] = useState<string | null>(null);
  const [clock, setClock] = useState(Date.now());
  const activeContactId = useRef(contactId);
  const handbackRefreshTimer = useRef<number | null>(null);
  activeContactId.current = contactId;

  const load = useCallback(async () => {
    if (activeContactId.current !== contactId) return;
    setLoading(true);
    try {
      const res = await messagesApi.getContactMessages(contactId);
      if (activeContactId.current === contactId) setMessages(res.data.data);
    } catch { /* ignore */ }
    if (activeContactId.current === contactId) setLoading(false);
  }, [contactId]);

  const loadContact = useCallback(async () => {
    try {
      const res = await contactsApi.getContact(contactId);
      if (activeContactId.current === contactId) setContact(res.data.data);
    } catch { /* ignore */ }
  }, [contactId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    let active = true;
    void aiApi.getStatus().then(
      (status) => { if (active) setAiEnabled(status.enabled); },
      () => { if (active) setAiEnabled(false); },
    );
    return () => { active = false; };
  }, []);
  useEffect(() => {
    setContact(null);
    setHandingBack(false);
    setBrief(null);
    setBriefing(false);
    setMessageTranslations({});
    setTranslatingId(null);
    loadContact();
    return () => {
      if (handbackRefreshTimer.current !== null) {
        window.clearTimeout(handbackRefreshTimer.current);
        handbackRefreshTimer.current = null;
      }
    };
  }, [loadContact]);

  useEffect(() => {
    setClock(Date.now());
  }, [channel, messages]);

  useEffect(() => {
    if (channel !== 'whatsapp') return undefined;
    const now = Date.now();
    const pending = messages
      .filter((message) => message.author_kind === 'agent' && !message.external_id)
      .map((message) => new Date(message.created_at).getTime() + 45000 - now)
      .filter((delay) => delay > 0);
    if (pending.length === 0) return undefined;
    const timer = window.setTimeout(() => setClock(Date.now()), Math.min(...pending));
    return () => window.clearTimeout(timer);
  }, [channel, clock, messages]);

  const handleSent = () => {
    load();
    loadContact();
  };

  const authorLabel = (message: Message) => {
    if (message.author_kind === 'agent') {
      return message.sent_by_name
        ? t('authorAgentWithName', { name: message.sent_by_name })
        : t('authorAgent');
    }
    if (message.author_kind === 'console') return t('authorConsole');
    if (message.author_kind !== 'ai') return null;
    if (!message.bot_id) return t('authorAi');
    const industryKeys: Record<string, string> = { food: 'food', hotel: 'hotel', realestate: 'realestate', retail: 'retail', saas: 'saas' };
    const industry = industryKeys[message.bot_id] ? t(`botIndustries.${industryKeys[message.bot_id]}`) : message.bot_id;
    return t('authorAiWithIndustry', { industry });
  };

  const deliveryLabel = (message: Message) => {
    if (channel === 'whatsapp' && message.author_kind === 'agent' && !message.external_id) {
      return clock < new Date(message.created_at).getTime() + 45000 ? t('sending') : t('sentUnconfirmed');
    }
    if (message.direction !== 'outbound' || !message.delivery_status) return null;
    if (message.delivery_status === 'failed') return message.delivery_error ? t('deliveryFailedWithError', { error: message.delivery_error }) : t('deliveryFailed');
    return t(`delivery${message.delivery_status.charAt(0).toUpperCase()}${message.delivery_status.slice(1)}`);
  };

  const isRetryable = (message: Message) => channel === 'whatsapp'
    && message.author_kind === 'agent'
    && !message.external_id
    && clock >= new Date(message.created_at).getTime() + 45000;

  const retry = async (message: Message) => {
    setRetryingId(message.id);
    try {
      const response = await messagesApi.retryWhatsApp(message.id);
      toast(response.status === 202 ? t('sentUnconfirmed') : t('retrySent'));
    } catch (error: unknown) {
      toast.error(retryErrorMessage(error, t));
    } finally {
      setRetryingId(null);
      load();
    }
  };

  const handback = async () => {
    if (!window.confirm(t('handbackConfirm'))) return;
    const requestedContactId = contactId;
    setHandingBack(true);
    try {
      const response = await contactsApi.handbackWhatsApp(requestedContactId);
      const handover: WhatsAppHandover = response.data.data;
      if (activeContactId.current !== requestedContactId) return;
      setContact((current) => current ? { ...current, ...handover } : current);
      if (isHandoverActive(handover)) {
        toast(t('handbackRequested'));
        if (handbackRefreshTimer.current !== null) window.clearTimeout(handbackRefreshTimer.current);
        handbackRefreshTimer.current = window.setTimeout(() => {
          handbackRefreshTimer.current = null;
          if (activeContactId.current === requestedContactId) void loadContact();
        }, 5000);
      } else {
        toast(t('handbackComplete'));
      }
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId) toast.error(handbackErrorMessage(error, t));
    } finally {
      if (activeContactId.current === requestedContactId) setHandingBack(false);
    }
  };

  const createBrief = async () => {
    const requestedContactId = contactId;
    setBriefing(true);
    try {
      const response = await aiApi.createConversationBrief(
        requestedContactId,
        briefOutputLanguage(i18n.language),
      );
      if (activeContactId.current === requestedContactId) {
        setBrief({ contactId: requestedContactId, data: response.data.data });
      }
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId) toast.error(briefErrorMessage(error, t));
    } finally {
      if (activeContactId.current === requestedContactId) setBriefing(false);
    }
  };

  const visibleBrief = brief?.contactId === contactId ? brief.data : null;

  const translateMessage = async (message: Message) => {
    const text = message.body.trim();
    if (!text || Array.from(text).length > 2000 || translatingId) return;
    const requestedContactId = contactId;
    setTranslatingId(message.id);
    try {
      const response = await aiApi.translate(requestedContactId, text, bubbleLanguage(i18n.language));
      if (activeContactId.current === requestedContactId) {
        setMessageTranslations((current) => ({ ...current, [message.id]: response.data.data.translation }));
      }
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId) toast.error(briefErrorMessage(error, t));
    } finally {
      if (activeContactId.current === requestedContactId) setTranslatingId(null);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-dark-border">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">{contactName}</h3>
            {contact?.wa_handover_updated_at && (
              <p className="text-xs text-text-secondary mt-1">
                {contact.wa_handover_active ? t('handoverActive', { expiresAt: dayjs(contact.wa_handover_expires_at).format('YYYY-MM-DD HH:mm') }) : t('aiReplying')}
              </p>
            )}
          </div>
          {channel === 'whatsapp' && contact && isHandoverActive(contact) && (
            <button type="button" className="btn-secondary text-xs" disabled={handingBack} onClick={handback}>
              {t('handbackToAi')}
            </button>
          )}
          {aiEnabled && (
            <button type="button" className="btn-secondary text-xs" disabled={briefing || messages.length === 0} onClick={createBrief}>
              {briefing ? t('aiBriefLoading') : t('aiBrief')}
            </button>
          )}
        </div>
        <p className="text-xs text-text-muted">{t('conversation')} · {channel}</p>
        {visibleBrief && (
          <section className="mt-3 rounded-lg border border-dark-border bg-dark-hover p-3 text-sm text-text-primary">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-text-secondary">{t('aiGeneratedUnverified')}</p>
              <button type="button" className="text-xs text-primary" onClick={() => setBrief(null)}>{t('aiBriefCollapse')}</button>
            </div>
            {visibleBrief.truncated && <p className="mb-2 text-xs text-text-muted">{t('aiBriefTruncated')}</p>}
            <dl className="space-y-2">
              <div><dt className="font-medium">{t('aiBriefCustomerNeed')}</dt><dd>{visibleBrief.customer_need}</dd></div>
              <div><dt className="font-medium">{t('aiBriefConversationState')}</dt><dd>{visibleBrief.conversation_state}</dd></div>
              <div><dt className="font-medium">{t('aiBriefCustomerMood')}</dt><dd>{t(`aiBriefMood.${visibleBrief.customer_mood}`)}</dd></div>
              <div><dt className="font-medium">{t('aiBriefOpenQuestions')}</dt><dd>{visibleBrief.open_questions.length ? <ul className="list-disc pl-5">{visibleBrief.open_questions.map((question, index) => <li key={`${index}-${question}`}>{question}</li>)}</ul> : t('aiBriefNone')}</dd></div>
              <div><dt className="font-medium">{t('aiBriefSuggestedNextStep')}</dt><dd>{visibleBrief.suggested_next_step || t('aiBriefNone')}</dd></div>
            </dl>
          </section>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {loading ? <p className="text-text-muted text-sm text-center py-4">...</p> : messages.length === 0 ? <p className="text-text-muted text-sm text-center py-4">{t('noMessages')}</p> : messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.direction === 'outbound' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[75%] rounded-xl px-3 py-2 ${msg.direction === 'outbound' ? 'bg-primary/20 text-text-primary' : 'bg-dark-hover text-text-primary'}`}>
              {msg.subject && <p className="text-xs font-medium text-text-secondary mb-1">{msg.subject}</p>}
              <p className="text-sm whitespace-pre-wrap break-words">{msg.body}</p>
              {aiEnabled && (
                <div className="mt-2">
                  <button type="button" className="text-xs text-primary" disabled={translatingId !== null || !msg.body.trim() || Array.from(msg.body.trim()).length > 2000} onClick={() => translateMessage(msg)}>
                    {translatingId === msg.id ? t('aiTranslationLoading') : t('aiTranslate')}
                  </button>
                  {(!msg.body.trim() || Array.from(msg.body.trim()).length > 2000) && <p className="text-[10px] text-text-muted">{!msg.body.trim() ? t('aiTranslationEmpty') : t('aiTranslationInputTooLong')}</p>}
                  {messageTranslations[msg.id] && <p className="mt-1 text-sm whitespace-pre-wrap break-words text-text-secondary">{messageTranslations[msg.id]}<span className="mt-1 block text-[10px] text-text-muted">{t('aiGeneratedUnverified')}</span></p>}
                </div>
              )}
              <div className="flex flex-wrap items-center justify-end gap-x-2 text-[10px] text-text-muted mt-1">
                {authorLabel(msg) && <span>{authorLabel(msg)}</span>}
                {deliveryLabel(msg) && <span>{deliveryLabel(msg)}</span>}
                <span>{dayjs(msg.created_at).format('HH:mm')}</span>
              </div>
              {isRetryable(msg) && (
                <button type="button" className="text-xs text-primary mt-1" disabled={retryingId === msg.id} onClick={() => retry(msg)}>
                  {t('retry')}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      <ReplyBox contactId={contactId} channel={channel} waReplyWindowUntil={contact?.wa_reply_window_until} contactPhone={contact?.phone} aiEnabled={aiEnabled} messageCount={messages.length} onSent={handleSent} />
    </div>
  );
}
