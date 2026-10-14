import { useState, type FormEvent } from 'react';
import axios from 'axios';
import dayjs from 'dayjs';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import { messagesApi } from '@/services/messages';

interface Props {
  contactId: string;
  channel: string;
  replyWindowUntil: string | null;
  onSent: () => Promise<void>;
}

export default function ReplyBox({ contactId, channel, replyWindowUntil, onSent }: Props) {
  const { t } = useTranslation('inbox');
  const [message, setMessage] = useState('');
  const [subject, setSubject] = useState('');
  const [sending, setSending] = useState(false);
  const whatsappWindowClosed = channel === 'whatsapp'
    && (!replyWindowUntil || !dayjs(replyWindowUntil).isAfter(dayjs()));

  const showSendError = (err: unknown) => {
    const response = axios.isAxiosError(err) ? err.response : undefined;
    const error = response?.data?.error;
    const code = error?.code;
    const fields = error?.fields || {};
    if (code === 'WINDOW_CLOSED') toast.error(t('sendWindowClosed', { closedAt: fields.window_closed_at || t('unknownTime') }));
    else if (code === 'SEND_FAILED') toast.error(fields.error_code || fields.error_title
      ? t('sendMetaRejected', { code: fields.error_code || t('unknownErrorCode'), title: fields.error_title || t('unknownErrorTitle') })
      : t('messageNotSent'));
    else if (code === 'NOT_CONFIGURED') toast.error(t('chatbotNotConfigured'));
    else if (code === 'NOT_ASSIGNED') toast.error(t('notAssigned'));
    else if (code === 'NO_PHONE') toast.error(t('noWhatsAppNumber'));
    else if (code === 'RETRY_EXPIRED') toast.error(t('retryExpired'));
    else if (code === 'IN_PROGRESS') toast.error(t('messageSending'));
    else if (response?.status === 422) toast.error(t('sendFailed'));
    else toast.error(error?.message || t('sendFailed'));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!message.trim()) return;
    setSending(true);
    try {
      if (channel === 'email') {
        await messagesApi.sendEmail(contactId, subject, message);
        setMessage('');
        setSubject('');
        await onSent();
      } else {
        const response = await messagesApi.sendWhatsApp(contactId, message);
        setMessage('');
        await onSent();
        if (response.status === 202) toast(t('messageUnconfirmed'));
      }
    } catch (err: unknown) {
      if (channel === 'whatsapp' && axios.isAxiosError(err) && !err.response) {
        await onSent();
        toast.error(t('sendOutcomeUnknown'));
      } else showSendError(err);
    }
    setSending(false);
  };

  return (
    <form onSubmit={handleSubmit} className="border-t border-dark-border p-3 space-y-2">
      {channel === 'email' && (
        <input
          className="input text-sm"
          placeholder={t('subject')}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
      )}
      <div className="flex gap-2">
        <textarea
          className="input flex-1 text-sm"
          placeholder={t('replyPlaceholder')}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          maxLength={channel === 'whatsapp' ? 4096 : undefined}
          disabled={sending || whatsappWindowClosed}
        />
        <button
          type="submit"
          disabled={sending || whatsappWindowClosed || !message.trim()}
          className="btn-primary text-sm px-4"
        >
          {t('send')}
        </button>
      </div>
      {whatsappWindowClosed && <p className="text-xs text-text-muted">{t('replyWindowClosed')}</p>}
    </form>
  );
}
