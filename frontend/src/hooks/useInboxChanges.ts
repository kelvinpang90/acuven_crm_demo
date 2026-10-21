import { useEffect, useRef } from 'react';
import { subscribeInboxChanges } from '@/services/inboxStream';

// Calls onChange whenever the server reports that messages changed.
export function useInboxChanges(onChange: () => void) {
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => subscribeInboxChanges(() => latest.current()), []);
}
