import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import { messagesApi, type ConversationListParams } from '@/services/messages';
import type { Conversation, Message, PaginatedResponse } from '@/types';
import MessageList from './MessageList';
import ConversationView from './ConversationView';
import Pagination from '@/components/common/Pagination';
import { useIsMobile } from '@/hooks/useBreakpoint';
import { useInboxChanges } from '@/hooks/useInboxChanges';
import { customerOf, farEndOf, openConversation } from '@/utils/conversation';

type ChannelFilter = 'all' | 'whatsapp' | 'email' | 'unread';

const PAGE_SIZE = 20;

export default function InboxPage() {
  const { t } = useTranslation('inbox');
  const isMobile = useIsMobile();
  const location = useLocation();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<ChannelFilter>('all');
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Message | null>(null);
  const requestSeq = useRef(0);
  const selectedCustomer = selected ? customerOf(selected) : undefined;

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
      const params: ConversationListParams = { page, page_size: PAGE_SIZE };
      if (filter === 'whatsapp' || filter === 'email') {
        params.channel = filter;
      } else if (filter === 'unread') {
        params.unread = true;
      }
      const res = await messagesApi.getConversations(params);
      const paged: PaginatedResponse<Conversation> = res.data.data;
      // Only the latest request may land; an older one would show stale rows.
      if (seq === requestSeq.current) {
        setConversations(paged.data);
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

  // Reads the whole conversation; one without a contact can only read this row.
  const markRead = async (message: Message) => {
    try {
      if (message.contact_id) await messagesApi.markContactRead(message.contact_id);
      else await messagesApi.markRead(message.id);
      const customer = customerOf(message);
      setConversations((prev) =>
        prev.map((c) => (customerOf(c) === customer ? { ...c, unread_count: 0 } : c))
      );
    } catch { /* ignore */ }
  };

  // The open conversation stays read: on opening, and when a pushed refresh
  // brings it new messages.
  useEffect(() => {
    const row = conversations.find((c) => customerOf(c) === selectedCustomer);
    if (row && row.unread_count > 0) void markRead(row);
  }, [conversations, selectedCustomer]);

  // Lets the new-message alert skip the conversation already on screen.
  useEffect(() => {
    openConversation.set(selectedCustomer ?? null);
    return () => openConversation.set(null);
  }, [selectedCustomer]);

  const handleSelect = (conversation: Conversation) => setSelected(conversation);

  // Opened from the new-message alert: show that conversation straight away.
  // It may not be on this page of the list, so read it here as well.
  const openMessage = (location.state as { openMessage?: Message } | null)?.openMessage;
  useEffect(() => {
    if (!openMessage) return;
    setSelected(openMessage);
    void markRead(openMessage);
    // Drop the state so a reload or going back does not reopen it.
    navigate(location.pathname, { replace: true, state: null });
  }, [openMessage]);

  return (
    <div className="h-[calc(100dvh-6rem)] md:h-[calc(100dvh-2rem)] lg:h-[calc(100dvh-3rem)] flex flex-col">
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
            ) : conversations.length === 0 ? (
              <p className="text-text-muted text-sm text-center py-8">{t('noMessages')}</p>
            ) : (
              <MessageList
                conversations={conversations}
                selectedCustomer={selectedCustomer}
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
                contactId={customerOf(selected)}
                contactName={selected.contact_name || farEndOf(selected)}
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
