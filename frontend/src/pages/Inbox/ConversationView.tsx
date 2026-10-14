import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { messagesApi } from '@/services/messages';
import { contactsApi } from '@/services/contacts';
import type { Contact, Message } from '@/types';
import dayjs from 'dayjs';
import ReplyBox from './ReplyBox';

interface Props {
  contactId: string;
  contactName: string;
  channel: string;
}

export default function ConversationView({ contactId, contactName, channel }: Props) {
  const { t } = useTranslation('inbox');
  const [messages, setMessages] = useState<Message[]>([]);
  const [contact, setContact] = useState<Contact | null>(null);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now());
  const [retryingMessageId, setRetryingMessageId] = useState<string | null>(null);
  const [handingBack, setHandingBack] = useState(false);
  const handbackRefreshTimer = useRef<number | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await messagesApi.getContactMessages(contactId);
      setNow(Date.now());
      setMessages(res.data.data);
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [contactId]);

  useEffect(() => () => {
    if (handbackRefreshTimer.current !== null) window.clearTimeout(handbackRefreshTimer.current);
  }, [contactId]);

  useEffect(() => {
    const currentTime = Date.now();
    const pendingTimes = messages.filter((message) => message.channel === 'whatsapp' && message.author_kind === 'agent' && !message.external_id)
      .map((message) => new Date(message.created_at).getTime() + 45000).filter((time) => time > currentTime);
    if (!pendingTimes.length) return undefined;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(...pendingTimes) - currentTime);
    return () => window.clearTimeout(timer);
  }, [messages, now]);

  useEffect(() => {
    let current = true;
    setContact(null);
    contactsApi.getContact(contactId)
      .then((res) => {
        if (current) setContact(res.data.data);
      })
      .catch(() => { /* ignore */ });
    return () => { current = false; };
  }, [contactId]);

  const handleSent = () => load();

  const loadContact = async () => {
    const response = await contactsApi.getContact(contactId);
    setContact(response.data.data);
  };

  const isHandoverActive = Boolean(contact?.wa_handover_active && contact.wa_handover_expires_at
    && dayjs(contact.wa_handover_expires_at).isAfter(dayjs()));

  const showActionError = (err: unknown) => {
    const response = axios.isAxiosError(err) ? err.response : undefined;
    const error = response?.data?.error;
    const code = error?.code;
    const fields = error?.fields || {};
    if (code === 'WINDOW_CLOSED') toast.error(t('sendWindowClosed', { closedAt: fields.window_closed_at || t('unknownTime') }));
    else if (code === 'SEND_FAILED') toast.error(fields.error_code || fields.error_title
      ? t('sendMetaRejected', { code: fields.error_code || t('unknownErrorCode'), title: fields.error_title || t('unknownErrorTitle') })
      : t('messageNotSent'));
    else if (code === 'IN_PROGRESS') toast.error(t('messageSending'));
    else if (code === 'RETRY_EXPIRED') toast.error(t('retryExpired'));
    else if (code === 'NOT_CONFIGURED') toast.error(t('chatbotNotConfigured'));
    else if (code === 'NOT_ASSIGNED') toast.error(t('notAssigned'));
    else if (code === 'NO_PHONE' || code === 'NO_IDENTITY') toast.error(t('noWhatsAppNumber'));
    else if (code === 'CHATBOT_FAILED') toast.error(t('handbackFailed'));
    else if (response?.status === 422) toast.error(t('sendFailed'));
    else toast.error(error?.message || t('sendFailed'));
  };

  const retryMessage = async (messageId: string) => {
    setRetryingMessageId(messageId);
    try {
      const response = await messagesApi.retryWhatsApp(messageId);
      await load();
      toast(response.status === 202 ? t('messageUnconfirmed') : t('retrySent'));
    } catch (err: unknown) {
      await load();
      if (axios.isAxiosError(err) && !err.response) toast.error(t('sendOutcomeUnknown'));
      else showActionError(err);
    } finally {
      setRetryingMessageId(null);
    }
  };

  const handbackToAi = async () => {
    if (!window.confirm(t('handbackConfirm'))) return;
    setHandingBack(true);
    try {
      const response = await contactsApi.handbackWhatsApp(contactId);
      setContact((current) => (current ? { ...current, ...response.data.data } : current));
      const remainsActive = response.data.data.wa_handover_active && response.data.data.wa_handover_expires_at
        && dayjs(response.data.data.wa_handover_expires_at).isAfter(dayjs());
      if (remainsActive) {
        toast(t('handbackRequested'));
        if (handbackRefreshTimer.current !== null) window.clearTimeout(handbackRefreshTimer.current);
        handbackRefreshTimer.current = window.setTimeout(() => { loadContact().catch(() => undefined); }, 5000);
      } else toast.success(t('handbackComplete'));
    } catch (err: unknown) {
      showActionError(err);
    } finally {
      setHandingBack(false);
    }
  };

  const authorLabel = (message: Message) => {
    if (message.author_kind === 'console') return t('authorConsole');
    if (message.author_kind === 'agent') return message.sent_by_name
      ? t('authorAgentWithName', { name: message.sent_by_name }) : t('authorAgent');
    if (message.author_kind !== 'ai') return null;
    if (!message.bot_id) return t('authorAi');

    const industryKeys: Record<string, string> = {
      food: 'food', hotel: 'hotel', realestate: 'realestate', retail: 'retail', saas: 'saas',
    };
    const industry = industryKeys[message.bot_id]
      ? t(`botIndustries.${industryKeys[message.bot_id]}`)
      : message.bot_id;
    return t('authorAiWithIndustry', { industry });
  };

  const deliveryLabel = (message: Message) => {
    if (message.direction !== 'outbound' || !message.delivery_status) return null;
    if (message.delivery_status === 'failed') {
      return message.delivery_error
        ? t('deliveryFailedWithError', { error: message.delivery_error })
        : t('deliveryFailed');
    }
    return t(`delivery${message.delivery_status.charAt(0).toUpperCase()}${message.delivery_status.slice(1)}`);
  };

  const pendingDeliveryLabel = (message: Message) => {
    if (message.channel !== 'whatsapp' || message.author_kind !== 'agent' || message.external_id) return null;
    return new Date(message.created_at).getTime() + 45000 > now ? t('messageSending') : t('messageUnconfirmed');
  };

  const canRetry = (message: Message) => message.channel === 'whatsapp' && message.author_kind === 'agent'
    && !message.external_id && new Date(message.created_at).getTime() + 45000 <= now;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-3 border-b border-dark-border">
        <h3 className="text-sm font-semibold text-text-primary">{contactName}</h3>
        {contact?.wa_handover_updated_at && (
          <p className="text-xs text-text-secondary mt-1">
            {isHandoverActive
              ? t('handoverActive', { expiresAt: dayjs(contact.wa_handover_expires_at).format('YYYY-MM-DD HH:mm') })
              : t('aiReplying')}
          </p>
        )}
        {isHandoverActive && (
          <button type="button" onClick={handbackToAi} disabled={handingBack} className="btn-secondary text-xs mt-2">
            {t('handbackToAi')}
          </button>
        )}
        <p className="text-xs text-text-muted">{t('conversation')} · {channel}</p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {loading ? (
          <p className="text-text-muted text-sm text-center py-4">...</p>
        ) : messages.length === 0 ? (
          <p className="text-text-muted text-sm text-center py-4">{t('noMessages')}</p>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.direction === 'outbound' ? 'justify-end' : 'justify-start'}`}
            >
              <div className={`max-w-[75%] rounded-xl px-3 py-2 ${
                msg.direction === 'outbound'
                  ? 'bg-primary/20 text-text-primary'
                  : 'bg-dark-hover text-text-primary'
              }`}>
                {msg.subject && (
                  <p className="text-xs font-medium text-text-secondary mb-1">{msg.subject}</p>
                )}
                <p className="text-sm whitespace-pre-wrap break-words">{msg.body}</p>
                <div className="flex flex-wrap items-center justify-end gap-x-2 text-[10px] text-text-muted mt-1">
                  {authorLabel(msg) && <span>{authorLabel(msg)}</span>}
                  {deliveryLabel(msg) && <span>{deliveryLabel(msg)}</span>}
                  {pendingDeliveryLabel(msg) && <span>{pendingDeliveryLabel(msg)}</span>}
                  {canRetry(msg) && <button type="button" onClick={() => retryMessage(msg.id)} disabled={retryingMessageId === msg.id} className="text-primary hover:underline">{t('retry')}</button>}
                  <span>{dayjs(msg.created_at).format('HH:mm')}</span>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Reply */}
      <ReplyBox contactId={contactId} channel={channel} replyWindowUntil={contact?.wa_reply_window_until ?? null} onSent={handleSent} />
    </div>
  );
}
