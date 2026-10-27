import dayjs from 'dayjs';
import { useTranslation } from 'react-i18next';
import type { Conversation, ConversationTriage, TriageUrgency } from '@/types';
import { customerOf, farEndOf } from '@/utils/conversation';

interface Props {
  conversations: Conversation[];
  selectedCustomer?: string;
  onSelect: (conversation: Conversation) => void;
}

const CHANNEL_ICONS: Record<string, string> = {
  whatsapp: '💬',
  email: '📧',
};

// Only these urgencies get a tag of their own; normal and low show in the hover only.
const URGENCY_CLASSES: Partial<Record<TriageUrgency, string>> = {
  urgent: 'bg-red-500/15 text-red-400',
  high: 'bg-orange-500/15 text-orange-400',
};

// The conversation's AI labels, shown whatever the direction of the latest message.
function TriageTags({ triage }: { triage: ConversationTriage }) {
  const { t } = useTranslation('inbox');
  const urgencyClass = URGENCY_CLASSES[triage.urgency];
  const hint = t('triageHint', {
    intent: t(`triageIntent.${triage.intent}`),
    urgency: t(`triageUrgency.${triage.urgency}`),
    time: dayjs(triage.triaged_at).format('YYYY-MM-DD HH:mm'),
  });
  return (
    <span className="flex items-center gap-1 shrink-0" title={hint}>
      <span className="px-1.5 py-0.5 rounded bg-dark-hover text-text-secondary text-[10px] leading-none">
        {t(`triageIntent.${triage.intent}`)}
      </span>
      {urgencyClass && (
        <span className={`px-1.5 py-0.5 rounded text-[10px] leading-none ${urgencyClass}`}>
          {t(`triageUrgency.${triage.urgency}`)}
        </span>
      )}
      <span className="text-[10px] text-text-muted leading-none">{t('triageAi')}</span>
    </span>
  );
}

// One row per customer: their latest message and how many inbound are unread.
export default function MessageList({ conversations, selectedCustomer, onSelect }: Props) {
  return (
    <div className="space-y-1">
      {conversations.map((conversation) => {
        const customer = customerOf(conversation);
        const unread = conversation.unread_count > 0;
        return (
          <button
            key={customer}
            onClick={() => onSelect(conversation)}
            className={`w-full text-left p-3 rounded-lg border transition-colors ${
              selectedCustomer === customer
                ? 'border-primary/50 bg-primary/5'
                : 'border-dark-border hover:bg-dark-hover'
            }`}
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm">{CHANNEL_ICONS[conversation.channel] || '💬'}</span>
              <span className={`text-sm font-medium truncate ${unread ? 'text-text-primary' : 'text-text-secondary'}`}>
                {conversation.contact_name || farEndOf(conversation)}
              </span>
              <span className="text-xs text-text-muted ml-auto shrink-0">
                {dayjs(conversation.created_at).format('MM/DD HH:mm')}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <p className={`text-xs truncate flex-1 ${unread ? 'text-text-secondary' : 'text-text-muted'}`}>
                {conversation.subject || conversation.body}
              </p>
              {conversation.triage && <TriageTags triage={conversation.triage} />}
              {unread && (
                <span className="min-w-[18px] h-[18px] px-1 rounded-full bg-primary text-white text-[10px] leading-[18px] text-center shrink-0">
                  {conversation.unread_count > 99 ? '99+' : conversation.unread_count}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
