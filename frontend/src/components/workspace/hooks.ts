'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import {
  DEFAULT_LAYOUT,
  readCachedLayout,
  writeCachedLayout,
  type WorkspaceLayout,
} from './types';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}

export function useDebouncedCallback<A extends unknown[]>(fn: (...args: A) => void, delayMs: number) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (...args: A) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => fnRef.current(...args), delayMs);
  };
}

export function useWorkspaceLayout(): {
  layout: WorkspaceLayout;
  setLayout: (update: Partial<WorkspaceLayout>) => void;
  ready: boolean;
} {
  const [layout, setLayoutState] = useState<WorkspaceLayout>(DEFAULT_LAYOUT);
  const [ready, setReady] = useState(false);
  const current = useRef(layout);
  current.current = layout;

  useEffect(() => {
    let cancelled = false;
    const cached = readCachedLayout();
    if (cached) setLayoutState(cached);
    (async () => {
      try {
        const data = await api.layouts.getWorkspace();
        if (!cancelled && data && data.layout) {
          setLayoutState(data.layout);
        }
      } catch {
        // 401/403/network: keep cache/defaults; auth handled at page level
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const persist = useDebouncedCallback((l: WorkspaceLayout) => {
    writeCachedLayout(l);
    try {
      api.layouts.saveWorkspace(l as unknown as Record<string, unknown>).catch(() => {
        // best-effort server persistence; local cache already updated
      });
    } catch {
      // ignore
    }
  }, 700);

  const setLayout = (update: Partial<WorkspaceLayout>) => {
    setLayoutState((prev) => {
      const next = {
        ...prev,
        ...update,
        panels: update.panels ?? { ...prev.panels },
      };
      persist(next);
      return next;
    });
  };

  return { layout, setLayout, ready };
}
