import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { messagesApi, type MessageListParams } from '@/services/messages';
import type { Message, PaginatedResponse } from '@/types';
import MessageList from './MessageList';
import ConversationView from './ConversationView';
import Pagination from '@/components/common/Pagination';
import { useIsMobile } from '@/hooks/useBreakpoint';
import { useInboxChanges } from '@/hooks/useInboxChanges';

type ChannelFilter = 'all' | 'whatsapp' | 'email' | 'unread';

const PAGE_SIZE = 20;

export default function InboxPage() {
  const { t } = useTranslation('inbox');
  const isMobile = useIsMobile();
  const [filter, setFilter] = useState<ChannelFilter>('all');
  const [messages, setMessages] = useState<Message[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Message | null>(null);
  const requestSeq = useRef(0);

  const filters: { key: ChannelFilter; label: string }[] = [
    { key: 'all', label: t('allMessages') },
    { key: 'whatsapp', label: t('whatsapp') },
    { key: 'email', label: t('emailChannel') },
    { key: 'unread', label: t('unread') },
  ];

  // silent: a pushed refresh, which keeps the current list on screen meanwhile.
  const load = useCallback(async (silent = false) => {
    const seq = ++requestSeq.current;
    if (!silent) setLoading(true);
    try {
      const params: MessageListParams = { page, page_size: PAGE_SIZE };
      if (filter === 'whatsapp' || filter === 'email') {
        params.channel = filter;
      } else if (filter === 'unread') {
        params.is_read = false;
      }
      const res = await messagesApi.getMessages(params);
      const paged: PaginatedResponse<Message> = res.data.data;
      // Only the latest request may land; an older one would show stale rows.
      if (seq === requestSeq.current) {
        setMessages(paged.data);
        setTotal(paged.total);
      }
    } catch { /* ignore */ }
    if (seq === requestSeq.current) setLoading(false);
  }, [filter, page]);

  useEffect(() => { load(); }, [load]);
  useInboxChanges(() => { void load(true); });

  const handleFilterChange = (f: ChannelFilter) => {
    setFilter(f);
    setPage(1);
    setSelected(null);
  };

  const handleSelect = async (msg: Message) => {
    setSelected(msg);
    if (!msg.is_read) {
      try {
        await messagesApi.markRead(msg.id);
        setMessages((prev) =>
          prev.map((m) => (m.id === msg.id ? { ...m, is_read: true } : m))
        );
      } catch { /* ignore */ }
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-text-primary">{t('title')}</h1>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-2 mb-4">
        {filters.map((f) => (
          <button
            key={f.key}
            onClick={() => handleFilterChange(f.key)}
            className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
              filter === f.key
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-dark-border text-text-secondary hover:bg-dark-hover'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Content: List + Conversation */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* Left: Message List — hidden on mobile when a message is selected */}
        <div
          className={`${
            isMobile && selected ? 'hidden' : 'flex'
          } w-full md:w-[380px] md:flex flex-col border border-dark-border rounded-xl bg-dark-card overflow-hidden shrink-0`}
        >
          <div className="flex-1 overflow-y-auto p-2">
            {loading ? (
              <p className="text-text-muted text-sm text-center py-8">...</p>
            ) : messages.length === 0 ? (
              <p className="text-text-muted text-sm text-center py-8">{t('noMessages')}</p>
            ) : (
              <MessageList
                messages={messages}
                selectedId={selected?.id}
                onSelect={handleSelect}
              />
            )}
          </div>
          {total > PAGE_SIZE && (
            <div className="px-3 border-t border-dark-border">
              <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
            </div>
          )}
        </div>

        {/* Right: Conversation — full screen on mobile when selected */}
        <div
          className={`${
            isMobile ? (selected ? 'flex' : 'hidden') : 'hidden md:flex'
          } flex-1 border border-dark-border rounded-xl bg-dark-card overflow-hidden flex-col`}
        >
          {selected ? (
            <>
              {isMobile && (
                <button
                  onClick={() => setSelected(null)}
                  className="flex items-center gap-2 px-4 py-3 border-b border-dark-border text-text-secondary hover:text-text-primary min-h-[44px]"
                  aria-label="Back"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                  </svg>
                  <span className="text-sm">{t('common:back', 'Back')}</span>
                </button>
              )}
              <ConversationView
                contactId={selected.contact_id || selected.sender_id}
                contactName={selected.contact_name || selected.sender_id}
                channel={selected.channel}
              />
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
              {t('noMessages')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
