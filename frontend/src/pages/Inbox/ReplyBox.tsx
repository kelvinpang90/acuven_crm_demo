import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { messagesApi } from '@/services/messages';
import type { ApiError } from '@/types';

interface Props {
  contactId: string;
  channel: string;
  waReplyWindowUntil: string | null | undefined;
  onSent: () => void;
}

function whatsappErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('sendUnknownResult');
  const apiError = error.response?.data?.error;
  if (!apiError) return t('sendFailed');
  const fields = apiError.fields || {};

  switch (apiError.code) {
    case 'WINDOW_CLOSED':
      return t('windowClosedAt', { closedAt: fields.window_closed_at || t('unknownTime') });
    case 'SEND_FAILED':
      return fields.error_code
        ? t('metaRejected', { code: fields.error_code, title: fields.error_title || '' })
        : t('sendNotSent');
    case 'NOT_CONFIGURED': return t('chatbotNotConfigured');
    case 'NOT_ASSIGNED': return t('notAssigned');
    case 'NO_PHONE': return t('noPhone');
    case 'RETRY_EXPIRED': return t('retryExpired');
    case 'IN_PROGRESS': return t('sendingInProgress');
    default: return t('sendFailed');
  }
}

export default function ReplyBox({ contactId, channel, waReplyWindowUntil, onSent }: Props) {
  const { t } = useTranslation('inbox');
  const [message, setMessage] = useState('');
  const [subject, setSubject] = useState('');
  const [sending, setSending] = useState(false);
  const [now, setNow] = useState(Date.now());
  const isWhatsApp = channel === 'whatsapp';
  const windowOpen = !isWhatsApp || (
    !!waReplyWindowUntil && new Date(waReplyWindowUntil).getTime() > now
  );

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

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!message.trim() || sending || !windowOpen) return;
    setSending(true);
    try {
      const response = channel === 'email'
        ? await messagesApi.sendEmail(contactId, subject, message)
        : await messagesApi.sendWhatsApp(contactId, message);
      setMessage('');
      setSubject('');
      onSent();
      if (isWhatsApp && response.status === 202) toast(t('sentUnconfirmed'));
    } catch (err: unknown) {
      if (channel === 'email') {
        const apiMsg = axios.isAxiosError<ApiError>(err) ? err.response?.data?.error?.message : undefined;
        toast.error(apiMsg || t('sendFailed'));
      } else if (!axios.isAxiosError(err) || !err.response) {
        onSent();
        toast.error(t('sendUnknownResult'));
      } else {
        toast.error(whatsappErrorMessage(err, t));
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="border-t border-dark-border p-3 space-y-2">
      {channel === 'email' && (
        <input className="input text-sm" placeholder={t('subject')} value={subject} onChange={(e) => setSubject(e.target.value)} />
      )}
      {isWhatsApp && !windowOpen && <p className="text-xs text-text-muted">{t('windowClosed')}</p>}
      <div className="flex gap-2">
        {isWhatsApp ? (
          <textarea
            className="input flex-1 text-sm resize-none"
            placeholder={t('replyPlaceholder')}
            value={message}
            maxLength={4096}
            rows={3}
            disabled={!windowOpen}
            onChange={(e) => setMessage(e.target.value)}
          />
        ) : (
          <input className="input flex-1 text-sm" placeholder={t('replyPlaceholder')} value={message} onChange={(e) => setMessage(e.target.value)} />
        )}
        <button type="submit" disabled={sending || !message.trim() || !windowOpen} className="btn-primary text-sm px-4">
          {t('send')}
        </button>
      </div>
    </form>
  );
}
