import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { messagesApi } from '@/services/messages';
import { contactsApi, type WhatsAppHandover } from '@/services/contacts';
import type { ApiError, Contact, Message } from '@/types';
import dayjs from 'dayjs';
import ReplyBox from './ReplyBox';

interface Props {
  contactId: string;
  contactName: string;
  channel: string;
}

function errorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error)) return t('sendUnknownResult');
  const apiError = error.response?.data?.error;
  if (!apiError) return t('sendUnknownResult');
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
    case 'CHATBOT_FAILED': return t('handbackFailed');
    default: return t('sendFailed');
  }
}

export default function ConversationView({ contactId, contactName, channel }: Props) {
  const { t } = useTranslation('inbox');
  const [messages, setMessages] = useState<Message[]>([]);
  const [contact, setContact] = useState<Contact | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [handingBack, setHandingBack] = useState(false);
  const [, setClock] = useState(Date.now());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await messagesApi.getContactMessages(contactId);
      setMessages(res.data.data);
    } catch { /* ignore */ }
    setLoading(false);
  }, [contactId]);

  const loadContact = useCallback(async () => {
    try {
      const res = await contactsApi.getContact(contactId);
      setContact(res.data.data);
    } catch { /* ignore */ }
  }, [contactId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    setContact(null);
    loadContact();
  }, [loadContact]);

  useEffect(() => {
    const pending = messages
      .filter((message) => message.author_kind === 'agent' && !message.external_id)
      .map((message) => new Date(message.created_at).getTime() + 45000 - Date.now())
      .filter((delay) => delay > 0);
    if (pending.length === 0) return undefined;
    const timer = window.setTimeout(() => setClock(Date.now()), Math.min(...pending));
    return () => window.clearTimeout(timer);
  }, [messages]);

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
    if (message.author_kind === 'agent' && !message.external_id) {
      return Date.now() < new Date(message.created_at).getTime() + 45000 ? t('sending') : t('sentUnconfirmed');
    }
    if (message.direction !== 'outbound' || !message.delivery_status) return null;
    if (message.delivery_status === 'failed') return message.delivery_error ? t('deliveryFailedWithError', { error: message.delivery_error }) : t('deliveryFailed');
    return t(`delivery${message.delivery_status.charAt(0).toUpperCase()}${message.delivery_status.slice(1)}`);
  };

  const isRetryable = (message: Message) => message.author_kind === 'agent'
    && !message.external_id
    && Date.now() >= new Date(message.created_at).getTime() + 45000;

  const retry = async (message: Message) => {
    setRetryingId(message.id);
    try {
      const response = await messagesApi.retryWhatsApp(message.id);
      toast(response.status === 202 ? t('sentUnconfirmed') : t('retrySent'));
    } catch (error: unknown) {
      toast.error(errorMessage(error, t));
    } finally {
      setRetryingId(null);
      load();
    }
  };

  const handback = async () => {
    if (!window.confirm(t('handbackConfirm'))) return;
    setHandingBack(true);
    try {
      const response = await contactsApi.handbackWhatsApp(contactId);
      const handover: WhatsAppHandover = response.data.data;
      setContact((current) => current ? { ...current, ...handover } : current);
      if (handover.wa_handover_active) {
        toast(t('handbackRequested'));
        window.setTimeout(() => { loadContact(); }, 5000);
      } else {
        toast(t('handbackComplete'));
      }
    } catch (error: unknown) {
      toast.error(errorMessage(error, t));
    } finally {
      setHandingBack(false);
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
          {channel === 'whatsapp' && contact?.wa_handover_active && (
            <button type="button" className="btn-secondary text-xs" disabled={handingBack} onClick={handback}>
              {t('handbackToAi')}
            </button>
          )}
        </div>
        <p className="text-xs text-text-muted">{t('conversation')} · {channel}</p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {loading ? <p className="text-text-muted text-sm text-center py-4">...</p> : messages.length === 0 ? <p className="text-text-muted text-sm text-center py-4">{t('noMessages')}</p> : messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.direction === 'outbound' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[75%] rounded-xl px-3 py-2 ${msg.direction === 'outbound' ? 'bg-primary/20 text-text-primary' : 'bg-dark-hover text-text-primary'}`}>
              {msg.subject && <p className="text-xs font-medium text-text-secondary mb-1">{msg.subject}</p>}
              <p className="text-sm whitespace-pre-wrap break-words">{msg.body}</p>
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

      <ReplyBox contactId={contactId} channel={channel} waReplyWindowUntil={contact?.wa_reply_window_until} onSent={handleSent} />
    </div>
  );
}
