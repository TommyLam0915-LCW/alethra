'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface OperationalStatus {
  mode: string;
  data_mode: string;
  broker_mode: string;
  ai_mode: string;
}

const DATA_MODE_LABELS: Record<string, string> = {
  demo: '演示數據',
  live: '即時數據',
};

const BROKER_MODE_LABELS: Record<string, string> = {
  'paper-only': '僅紙上模擬',
  sandbox: '沙盒',
  live: '即時',
};

const AI_MODE_LABELS: Record<string, string> = {
  mock: 'AI 模擬',
  live: 'AI 即時',
};

export default function StatusChips() {
  const [status, setStatus] = useState<OperationalStatus | null>(null);
  const [usageCount, setUsageCount] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const s = await api.billing.getOperationalStatus();
        if (!cancelled) setStatus(s);
      } catch {
        // leave chips hidden on failure
      }
      try {
        const events = await api.pilot.getMyUsageEvents();
        if (!cancelled) setUsageCount(Array.isArray(events) ? events.length : null);
      } catch {
        // usage unknown — chip stays hidden
      }
    };
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="ws-chips" data-testid="status-chips">
      {status && (
        <span
          className={`ws-chip ${status.data_mode === 'live' ? 'ws-chip-ok' : 'ws-chip-muted'}`}
          title="市場數據模式"
        >
          數據：{DATA_MODE_LABELS[status.data_mode] || status.data_mode}
        </span>
      )}
      {status && (
        <span
          className={`ws-chip ${status.broker_mode === 'live' ? 'ws-chip-warn' : 'ws-chip-muted'}`}
          title="經紀連接模式"
        >
          經紀：{BROKER_MODE_LABELS[status.broker_mode] || status.broker_mode}
        </span>
      )}
      {status && (
        <span className="ws-chip ws-chip-muted" title="個人 AI 模式">
          AI：{AI_MODE_LABELS[status.ai_mode] || status.ai_mode}
        </span>
      )}
      {usageCount !== null && (
        <span className="ws-chip ws-chip-muted" title="最近使用紀錄次數">
          用量：{usageCount}
        </span>
      )}
    </div>
  );
}
