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
  hasSavedState: boolean;
} {
  const [layout, setLayoutState] = useState<WorkspaceLayout>(DEFAULT_LAYOUT);
  const [ready, setReady] = useState(false);
  const [hasSavedState, setHasSavedState] = useState(false);
  const current = useRef(layout);
  current.current = layout;
  const userIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const cached = readCachedLayout();
    if (cached) {
      setLayoutState(cached);
      setHasSavedState(true);
    }
    (async () => {
      try {
        const data = await api.layouts.getWorkspace();
        if (!cancelled && data && data.layout) {
          setLayoutState(data.layout);
          setHasSavedState(true);
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

  const persist = useDebouncedCallback((l: WorkspaceLayout, initiatingUserId: string | null) => {
    // CORRECT: Only write to cache if the initiating user is still the current user
    const currentUserId = typeof window !== 'undefined'
      ? window.localStorage.getItem('token')
      : null;

    if (initiatingUserId !== currentUserId) {
      // Stale save attempt — discard it
      return;
    }

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
      // Capture the current user ID at the time of the change
      const currentUserId = typeof window !== 'undefined'
        ? window.localStorage.getItem('token')
        : null;
      persist(next, currentUserId);
      // Mark that user has made changes (has saved state)
      setHasSavedState(true);
      return next;
    });
  };

  return { layout, setLayout, ready, hasSavedState };
}

export function useWorkspacePreferences(ready: boolean, hasSavedState: boolean, setLayout: (update: any) => void) {
  const appliedRef = useRef(false);
  const hasSavedStateRef = useRef(hasSavedState);

  // Keep ref in sync with current value
  useEffect(() => {
    hasSavedStateRef.current = hasSavedState;
  }, [hasSavedState]);

  useEffect(() => {
    if (!ready || appliedRef.current || hasSavedState) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.layouts.getWorkspacePreferences();
        if (cancelled || !data.preferences) return;
        // Recheck hasSavedState after API response - user may have made changes while loading
        if (hasSavedStateRef.current) return;
        const prefs = data.preferences;
        const updates: any = {};
        // Apply defaults only if no saved state exists
        if (prefs.default_symbol) {
          updates.symbol = prefs.default_symbol;
        }
        if (prefs.default_range) {
          updates.range = prefs.default_range;
        }
        if (Object.keys(updates).length > 0) {
          setLayout(updates);
        }
        appliedRef.current = true;
      } catch {
        // best-effort; keep current state
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, hasSavedState, setLayout]);
}
