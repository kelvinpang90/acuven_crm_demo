import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { messagesApi } from '@/services/messages';
import { useInboxChanges } from '@/hooks/useInboxChanges';
import type { Message } from '@/types';
import { customerOf, farEndOf, openConversation } from '@/utils/conversation';

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

// One row per customer: their newest unseen message and how many arrived.
interface Alert {
  latest: Message;
  count: number;
}

// Top-right card for new customer messages, on every page except for the
// conversation already open in the inbox. Reads the latest messages through the
// normal, permission-scoped list.
export default function NewMessageAlert() {
  const { t } = useTranslation('inbox');
  const navigate = useNavigate();
  const [alerts, setAlerts] = useState<Alert[]>([]);
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
      const open = openConversation.get();
      const fresh = latest.filter(
        (m) => m.direction === 'inbound' && !known.has(m.id) && customerOf(m) !== open
      );
      latest.forEach((m) => known.add(m.id));
      if (fresh.length === 0) return;
      setAlerts((prev) => {
        // Newest first: the list arrives newest first, so the first hit is the latest.
        const merged = new Map<string, Alert>();
        for (const m of fresh) {
          const key = customerOf(m);
          const hit = merged.get(key);
          if (hit) hit.count += 1;
          else merged.set(key, { latest: m, count: 1 });
        }
        for (const alert of prev) {
          const hit = merged.get(customerOf(alert.latest));
          if (hit) hit.count += alert.count;
          else merged.set(customerOf(alert.latest), alert);
        }
        return [...merged.values()];
      });
    } catch { /* ignore */ }
  });

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
          🔔 {t('newMessages')} ({alerts.reduce((sum, alert) => sum + alert.count, 0)})
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
        {alerts.slice(0, MAX_LISTED).map(({ latest, count }) => (
          <button
            key={customerOf(latest)}
            type="button"
            onClick={() => open(latest)}
            className="w-full text-left p-2 rounded-lg bg-dark-hover hover:bg-primary/10 text-sm"
          >
            <span className="flex items-center gap-2">
              <span>{CHANNEL_ICONS[latest.channel] || '📨'}</span>
              <span className="font-medium text-text-primary truncate">
                {latest.contact_name || farEndOf(latest)}
              </span>
              {count > 1 && (
                <span className="ml-auto shrink-0 text-xs text-primary">{t('newMessagesCount', { count })}</span>
              )}
            </span>
            <span className="block text-text-secondary text-xs truncate mt-1">{preview(latest.body)}</span>
          </button>
        ))}
        {alerts.length > MAX_LISTED && (
          <p className="text-xs text-text-muted">{t('moreNewMessages', { count: alerts.length - MAX_LISTED })}</p>
        )}
      </div>
    </div>
  );
}
