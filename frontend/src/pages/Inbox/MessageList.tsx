import dayjs from 'dayjs';
import type { Conversation } from '@/types';
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
