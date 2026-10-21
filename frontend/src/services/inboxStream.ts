import api from './api';
import { authApi } from './auth';

// One server-sent-events connection shared by every inbox view on the page.
// The server only says "messages changed"; listeners refetch through the normal
// API. fetch, not EventSource: the token goes in a header, never in the URL.

type Listener = () => void;

const FIRST_RETRY_MS = 1000;
const MAX_RETRY_MS = 30000;
// The server sends a heartbeat every 20s. Silence this long means a dead
// connection a proxy or a sleeping laptop never closed: drop it and reconnect.
const STALL_MS = 45000;

const listeners = new Set<Listener>();
let controller: AbortController | null = null;
let retryTimer: number | null = null;
let retryMs = FIRST_RETRY_MS;

function notify() {
  listeners.forEach((listener) => listener());
}

function streamUrl() {
  return `${(api.defaults.baseURL || '/api').replace(/\/$/, '')}/messages/stream`;
}

async function connect() {
  const current = new AbortController();
  controller = current;
  let stallTimer: number | undefined;
  const expectData = () => {
    window.clearTimeout(stallTimer);
    stallTimer = window.setTimeout(() => current.abort(), STALL_MS);
  };
  try {
    const res = await fetch(streamUrl(), {
      headers: {
        Accept: 'text/event-stream',
        Authorization: `Bearer ${localStorage.getItem('access_token') ?? ''}`,
      },
      cache: 'no-store',
      signal: current.signal,
    });
    if (res.status === 401) {
      // The shared client refreshes the token, or sends the user to login.
      await authApi.getMe().catch(() => undefined);
      throw new Error('stream unauthorized');
    }
    if (!res.ok || !res.body) throw new Error(`stream status ${res.status}`);
    retryMs = FIRST_RETRY_MS;
    // Catch up on whatever changed before the stream was open.
    notify();
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    for (;;) {
      expectData();
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const events = buffer.split('\n\n');
      buffer = events.pop() ?? '';
      if (events.some((event) => event.split('\n').includes('event: messages_changed'))) notify();
    }
  } catch {
    /* aborted, stalled, network error or refused: reconnect below unless stopped */
  }
  window.clearTimeout(stallTimer);
  if (controller !== current) return;
  controller = null;
  scheduleReconnect();
}

function scheduleReconnect() {
  if (listeners.size === 0 || document.hidden) return;
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    void connect();
  }, retryMs);
  retryMs = Math.min(retryMs * 2, MAX_RETRY_MS);
}

function start() {
  if (controller || retryTimer !== null || document.hidden) return;
  void connect();
}

function stop() {
  controller?.abort();
  controller = null;
  if (retryTimer !== null) window.clearTimeout(retryTimer);
  retryTimer = null;
  retryMs = FIRST_RETRY_MS;
}

// A hidden tab drops its connection; coming back reconnects, which catches up.
function onVisibilityChange() {
  if (document.hidden) stop();
  else start();
}

export function subscribeInboxChanges(listener: Listener): () => void {
  listeners.add(listener);
  if (listeners.size === 1) document.addEventListener('visibilitychange', onVisibilityChange);
  start();
  return () => {
    listeners.delete(listener);
    if (listeners.size > 0) return;
    document.removeEventListener('visibilitychange', onVisibilityChange);
    stop();
  };
}
