import { useState, useEffect } from 'react';
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

  const load = async () => {
    setLoading(true);
    try {
      const res = await messagesApi.getContactMessages(contactId);
      setMessages(res.data.data);
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [contactId]);

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

  const authorLabel = (message: Message) => {
    if (message.author_kind === 'console') return t('authorConsole');
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

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-3 border-b border-dark-border">
        <h3 className="text-sm font-semibold text-text-primary">{contactName}</h3>
        {contact?.wa_handover_updated_at && (
          <p className="text-xs text-text-secondary mt-1">
            {contact.wa_handover_active
              ? t('handoverActive', { expiresAt: dayjs(contact.wa_handover_expires_at).format('YYYY-MM-DD HH:mm') })
              : t('aiReplying')}
          </p>
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
                  <span>{dayjs(msg.created_at).format('HH:mm')}</span>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Reply */}
      <ReplyBox contactId={contactId} channel={channel} onSent={handleSent} />
    </div>
  );
}
