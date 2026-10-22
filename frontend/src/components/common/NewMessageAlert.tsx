import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import { messagesApi } from '@/services/messages';
import { useInboxChanges } from '@/hooks/useInboxChanges';
import type { Message } from '@/types';

const LATEST_COUNT = 20;
const MAX_LISTED = 3;
const PREVIEW_CHARS = 60;
const AUTO_HIDE_MS = 10000;

const CHANNEL_ICONS: Record<string, string> = {
  whatsapp: '💬',
  email: '📧',
};

function preview(body: string) {
  const chars = Array.from(body.trim());
  return chars.length > PREVIEW_CHARS ? `${chars.slice(0, PREVIEW_CHARS).join('')}…` : chars.join('');
}

// Top-right card for customer messages that arrive while the user is outside the
// inbox. Reads the latest messages through the normal, permission-scoped list.
export default function NewMessageAlert() {
  const { t } = useTranslation('inbox');
  const navigate = useNavigate();
  const onInbox = useLocation().pathname.startsWith('/inbox');
  const onInboxRef = useRef(onInbox);
  onInboxRef.current = onInbox;
  const [alerts, setAlerts] = useState<Message[]>([]);
  // Ids already accounted for; null until the first look, which only records them.
  const seen = useRef<Set<string> | null>(null);

  useInboxChanges(async () => {
    try {
      const res = await messagesApi.getMessages({ page: 1, page_size: LATEST_COUNT });
      const latest = res.data.data.data;
      const known = seen.current;
      if (!known) {
        seen.current = new Set(latest.map((m) => m.id));
        return;
      }
      const fresh = latest.filter((m) => m.direction === 'inbound' && !known.has(m.id));
      latest.forEach((m) => known.add(m.id));
      // The inbox list updates live; no card there, and nothing saved for later.
      if (fresh.length === 0 || onInboxRef.current) return;
      setAlerts((prev) => [...fresh, ...prev.filter((m) => !fresh.some((f) => f.id === m.id))]);
    } catch { /* ignore */ }
  });

  useEffect(() => {
    if (onInbox) setAlerts([]);
  }, [onInbox]);

  // Restarts whenever more messages arrive.
  useEffect(() => {
    if (alerts.length === 0) return undefined;
    const timer = window.setTimeout(() => setAlerts([]), AUTO_HIDE_MS);
    return () => window.clearTimeout(timer);
  }, [alerts]);

  if (alerts.length === 0) return null;

  const open = (message: Message) => {
    setAlerts([]);
    navigate('/inbox', { state: { openMessage: message } });
  };

  return (
    <div className="bg-dark-card border border-primary/30 rounded-xl shadow-lg shadow-primary/10 p-4 animate-in fade-in slide-in-from-top-2">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-text-primary">
          🔔 {t('newMessages')} ({alerts.length})
        </h4>
        <button
          onClick={() => setAlerts([])}
          className="text-text-muted hover:text-text-primary text-sm"
          aria-label={t('common:close')}
        >
          ✕
        </button>
      </div>
      <div className="space-y-2">
        {alerts.slice(0, MAX_LISTED).map((message) => (
          <button
            key={message.id}
            type="button"
            onClick={() => open(message)}
            className="w-full text-left p-2 rounded-lg bg-dark-hover hover:bg-primary/10 text-sm"
          >
            <span className="flex items-center gap-2">
              <span>{CHANNEL_ICONS[message.channel] || '📨'}</span>
              <span className="font-medium text-text-primary truncate">
                {message.contact_name || t('unknownContact')}
              </span>
            </span>
            <span className="block text-text-secondary text-xs truncate mt-1">{preview(message.body)}</span>
          </button>
        ))}
        {alerts.length > MAX_LISTED && (
          <p className="text-xs text-text-muted">{t('moreNewMessages', { count: alerts.length - MAX_LISTED })}</p>
        )}
      </div>
    </div>
  );
}
