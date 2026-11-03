import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import axios from 'axios';
import type { TFunction } from 'i18next';
import { messagesApi } from '@/services/messages';
import { activeClaim, contactsApi, type WhatsAppHandover } from '@/services/contacts';
import { aiApi } from '@/services/ai';
import { usersApi } from '@/services/users';
import { useAuthStore } from '@/store/authStore';
import type { AiConversationBrief, AiLanguage, ApiError, Contact, Message, ReplyClaim, User } from '@/types';
import dayjs from 'dayjs';
import { useInboxChanges } from '@/hooks/useInboxChanges';
import ReplyBox, { claimErrorMessage, claimedMessage } from './ReplyBox';

interface Props {
  contactId: string;
  contactName: string;
  channel: string;
  // The inbox's 30-second clock, shared with the list tags.
  now: number;
  onListChange: () => void;
  // The user can no longer see this conversation.
  onClose: () => void;
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
    case 'CLAIMED': return claimedMessage(apiError.fields, t);
    default: return t('sendFailed');
  }
}

function reassignErrorMessage(error: unknown, t: TFunction<'inbox'>) {
  if (!axios.isAxiosError<ApiError>(error) || !error.response) return t('reassignFailed');
  switch (error.response.data?.error?.code) {
    case 'FORBIDDEN': return t('reassignForbidden');
    case 'NOT_FOUND': return t('notAssigned');
    default: return t('reassignFailed');
  }
}

// A read the user may no longer make: the contact answers 404, its messages 403.
function isRefused(error: unknown) {
  return axios.isAxiosError(error) && (error.response?.status === 403 || error.response?.status === 404);
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

const PAGE_SIZE = 50;

// open: the latest page replaces what is shown, read from the bottom.
// refresh: the latest page joins the older pages already shown; follows new
//   messages only when the reader is already at the bottom.
// sent: as refresh, then to the bottom to show what was just sent.
type LoadMode = 'open' | 'refresh' | 'sent';

// The latest page under the older pages already shown. When they do not meet
// (more new messages than a page), the page stands alone.
function joinLatest(shown: Message[], page: Message[]) {
  const joint = page.length ? shown.findIndex((message) => message.id === page[0].id) : -1;
  return joint >= 0 ? [...shown.slice(0, joint), ...page] : null;
}

export default function ConversationView({ contactId, contactName, channel, now, onListChange, onClose }: Props) {
  const { t, i18n } = useTranslation('inbox');
  const currentUser = useAuthStore((s) => s.user);
  const canReassign = currentUser?.role === 'admin' || currentUser?.role === 'manager';
  const [users, setUsers] = useState<User[]>([]);
  const [claiming, setClaiming] = useState(false);
  const [reassigning, setReassigning] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [contact, setContact] = useState<Contact | null>(null);
  const [loading, setLoading] = useState(true);
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [handingBack, setHandingBack] = useState(false);
  const [aiEnabled, setAiEnabled] = useState(false);
  const [briefing, setBriefing] = useState(false);
  const [brief, setBrief] = useState<{ contactId: string; data: AiConversationBrief } | null>(null);
  const [messageTranslations, setMessageTranslations] = useState<Record<string, string>>({});
  const [translatingId, setTranslatingId] = useState<string | null>(null);
  const [clock, setClock] = useState(Date.now());
  const activeContactId = useRef(contactId);
  const previousContactId = useRef(contactId);
  const messageTranslationRequestVersion = useRef(0);
  const handbackRefreshTimer = useRef<number | null>(null);
  const loadSeq = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(false);
  // Distance from the bottom to restore after older messages land on top.
  const keepFromBottom = useRef<number | null>(null);
  const shown = useRef(messages);
  if (previousContactId.current !== contactId) {
    previousContactId.current = contactId;
    messageTranslationRequestVersion.current += 1;
  }
  activeContactId.current = contactId;
  shown.current = messages;

  // Resolves true when the read was refused (403 or 404).
  const load = useCallback(async (mode: LoadMode = 'open') => {
    if (activeContactId.current !== contactId) return false;
    const seq = ++loadSeq.current;
    let refused = false;
    if (mode === 'open') setLoading(true);
    try {
      const res = await messagesApi.getContactMessages(contactId, { limit: PAGE_SIZE });
      if (activeContactId.current === contactId && seq === loadSeq.current) {
        const page = res.data.data;
        const list = listRef.current;
        stickToBottom.current = mode !== 'refresh'
          || (!!list && list.scrollHeight - list.scrollTop - list.clientHeight < 80);
        keepFromBottom.current = null;
        if (mode === 'open' || !joinLatest(shown.current, page)) setHasOlder(page.length === PAGE_SIZE);
        setMessages((current) => (mode === 'open' ? page : joinLatest(current, page) ?? page));
      }
    } catch (error: unknown) {
      refused = isRefused(error);
    }
    if (activeContactId.current === contactId && seq === loadSeq.current) setLoading(false);
    return refused;
  }, [contactId]);

  const loadOlder = async () => {
    const oldest = shown.current[0];
    if (!oldest || loadingOlder) return;
    const requestedContactId = contactId;
    setLoadingOlder(true);
    try {
      const res = await messagesApi.getContactMessages(requestedContactId, { before: oldest.id, limit: PAGE_SIZE });
      if (activeContactId.current !== requestedContactId) return;
      const older = res.data.data;
      const list = listRef.current;
      if (list) keepFromBottom.current = list.scrollHeight - list.scrollTop;
      setHasOlder(older.length === PAGE_SIZE);
      setMessages((current) => (current[0]?.id === oldest.id ? [...older, ...current] : current));
    } catch { /* ignore */ } finally {
      if (activeContactId.current === requestedContactId) setLoadingOlder(false);
    }
  };

  const handleScroll = () => {
    if (hasOlder && !loadingOlder && (listRef.current?.scrollTop ?? 0) < 80) void loadOlder();
  };

  // Resolves true when the read was refused (403 or 404).
  const loadContact = useCallback(async () => {
    try {
      const res = await contactsApi.getContact(contactId);
      if (activeContactId.current === contactId) setContact(res.data.data);
      return false;
    } catch (error: unknown) {
      return isRefused(error);
    }
  }, [contactId]);

  useEffect(() => { load(); }, [load]);
  useLayoutEffect(() => {
    const list = listRef.current;
    if (stickToBottom.current) {
      stickToBottom.current = false;
      if (list) list.scrollTop = list.scrollHeight;
    } else if (keepFromBottom.current !== null) {
      if (list) list.scrollTop = list.scrollHeight - keepFromBottom.current;
      keepFromBottom.current = null;
    }
  }, [messages]);
  // An inbound message also moves the WhatsApp reply window on the contact.
  useInboxChanges(() => {
    void load('refresh');
    void loadContact();
  });
  useEffect(() => {
    let active = true;
    void aiApi.getStatus().then(
      (status) => { if (active) setAiEnabled(status.enabled); },
      () => { if (active) setAiEnabled(false); },
    );
    return () => { active = false; };
  }, []);
  // Candidates are the users the backend lets this user assign to.
  useEffect(() => {
    if (!canReassign) return undefined;
    let active = true;
    void usersApi.getUsers().then(
      (res) => { if (active) setUsers(res.data.data ?? []); },
      () => { if (active) setUsers([]); },
    );
    return () => { active = false; };
  }, [canReassign]);
  useEffect(() => {
    setContact(null);
    setHasOlder(false);
    setLoadingOlder(false);
    setHandingBack(false);
    setClaiming(false);
    setReassigning(false);
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
    load('sent');
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
      load('refresh');
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

  const claim = contact ? activeClaim(contact, now) : null;
  const claimIsMine = !!claim && claim.userId === currentUser?.id;
  const claimLabel = !claim
    ? t('claimNone')
    : claimIsMine
      ? t('claimMine', { until: dayjs(claim.until).format('HH:mm') })
      : t('claimOther', { name: claim.name, until: dayjs(claim.until).format('HH:mm') });

  const applyClaim = (requestedContactId: string, next: ReplyClaim) => {
    if (activeContactId.current !== requestedContactId) return;
    setContact((current) => current ? {
      ...current,
      reply_claimed_by: next.reply_claimed_by,
      reply_claimed_by_name: next.reply_claimed_by_name,
      reply_claimed_until: next.reply_claimed_until,
    } : current);
  };

  // Claim when nobody holds it, release one's own, take over someone else's.
  const changeClaim = async () => {
    if (claim && !claimIsMine && !window.confirm(t('takeoverConfirm', { name: claim.name, until: dayjs(claim.until).format('HH:mm') }))) return;
    const requestedContactId = contactId;
    setClaiming(true);
    try {
      if (claimIsMine) {
        const response = await contactsApi.releaseClaim(requestedContactId);
        applyClaim(requestedContactId, response.data.data);
      } else {
        const response = await contactsApi.claim(requestedContactId, !!claim);
        const result = response.data.data;
        applyClaim(requestedContactId, result);
        if (result.taken_over_from && activeContactId.current === requestedContactId) {
          toast(t('takenOver', { name: result.taken_over_from_name || result.taken_over_from }));
        }
      }
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId) toast.error(claimErrorMessage(error, t));
    } finally {
      if (activeContactId.current === requestedContactId) setClaiming(false);
    }
  };

  // Changes only the owner, through the contact update. The new owner may put
  // the conversation out of this user's reach; then it is closed.
  const reassign = async (userId: string) => {
    const user = users.find((candidate) => candidate.id === userId);
    if (!user || !window.confirm(t('reassignConfirm', { name: user.name }))) return;
    const requestedContactId = contactId;
    setReassigning(true);
    try {
      await contactsApi.updateContact(requestedContactId, { assigned_to: user.id });
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId) {
        toast.error(reassignErrorMessage(error, t));
        setReassigning(false);
      }
      return;
    }
    if (activeContactId.current !== requestedContactId) return;
    toast(t('reassigned', { name: user.name }));
    const [contactRefused, messagesRefused] = await Promise.all([loadContact(), load('refresh')]);
    if (activeContactId.current !== requestedContactId) return;
    setReassigning(false);
    if (contactRefused || messagesRefused) {
      toast(t('reassignNoAccess'));
      onClose();
    } else {
      onListChange();
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
    const requestVersion = ++messageTranslationRequestVersion.current;
    setTranslatingId(message.id);
    try {
      const response = await aiApi.translate(requestedContactId, text, bubbleLanguage(i18n.language));
      if (activeContactId.current === requestedContactId && messageTranslationRequestVersion.current === requestVersion) {
        setMessageTranslations((current) => ({ ...current, [message.id]: response.data.data.translation }));
      }
    } catch (error: unknown) {
      if (activeContactId.current === requestedContactId && messageTranslationRequestVersion.current === requestVersion) toast.error(briefErrorMessage(error, t));
    } finally {
      if (activeContactId.current === requestedContactId && messageTranslationRequestVersion.current === requestVersion) setTranslatingId(null);
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="p-3 border-b border-dark-border">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">{contactName}</h3>
            {contact?.wa_handover_updated_at && (
              <p className="text-xs text-text-secondary mt-1">
                {contact.wa_handover_active ? t('handoverActive', { expiresAt: dayjs(contact.wa_handover_expires_at).format('YYYY-MM-DD HH:mm') }) : t('aiReplying')}
              </p>
            )}
            {channel === 'whatsapp' && contact && (
              <p className="text-xs text-text-secondary mt-1">{claimLabel}</p>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
          {channel === 'whatsapp' && contact && (
            <button type="button" className="btn-secondary text-xs" disabled={claiming} onClick={changeClaim}>
              {!claim ? t('claim') : claimIsMine ? t('releaseClaim') : t('takeover')}
            </button>
          )}
          {canReassign && contact && (
            <select className="input w-auto text-xs" value="" disabled={reassigning || users.length === 0} onChange={(event) => void reassign(event.target.value)}>
              <option value="" disabled>{t('reassign')}</option>
              {users.filter((user) => user.id !== contact.assigned_to).map((user) => (
                <option key={user.id} value={user.id}>{user.name}</option>
              ))}
            </select>
          )}
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

      <div ref={listRef} onScroll={handleScroll} className="flex-1 overflow-y-auto p-3 space-y-3">
        {!loading && messages.length > 0 && (
          <div className="text-center text-xs text-text-muted">
            {hasOlder ? (
              <button type="button" className="text-primary" disabled={loadingOlder} onClick={() => void loadOlder()}>
                {loadingOlder ? t('loadingOlder') : t('loadOlder')}
              </button>
            ) : t('noOlder')}
          </div>
        )}
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
                {msg.author_kind === 'agent' && msg.template_name && <span>{t('templateLabel', { name: msg.template_name })}</span>}
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

      <ReplyBox contactId={contactId} contactName={contact?.name || contactName} channel={channel} waReplyWindowUntil={contact?.wa_reply_window_until} contactPhone={contact?.phone} aiEnabled={aiEnabled} messageCount={messages.length} onSent={handleSent} onClaimChange={(next) => applyClaim(contactId, next)} />
    </div>
  );
}
