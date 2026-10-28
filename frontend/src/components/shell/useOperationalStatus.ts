'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

export interface OperationalStatus {
  mode: string;
  data_mode: string;
  broker_mode: string;
  ai_mode: string;
}

/**
 * Shared source of truth for the persistent context bar and status chips.
 * Polls the operational-status endpoint so every page shows the same
 * data/broker/AI mode without each page refetching on its own schedule.
 */
export function useOperationalStatus(pollMs = 60_000): {
  status: OperationalStatus | null;
  usageCount: number | null;
} {
  const [status, setStatus] = useState<OperationalStatus | null>(null);
  const [usageCount, setUsageCount] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const s = await api.billing.getOperationalStatus();
        if (!cancelled) setStatus(s);
      } catch {
        // leave status null on failure; the context bar hides the chip
      }
      try {
        const events = await api.pilot.getMyUsageEvents();
        if (!cancelled) setUsageCount(Array.isArray(events) ? events.length : null);
      } catch {
        // usage unknown — chip stays hidden
      }
    };
    load();
    const timer = setInterval(load, pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pollMs]);

  return { status, usageCount };
}
