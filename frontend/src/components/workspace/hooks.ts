'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import {
  DEFAULT_LAYOUT,
  mergeLayout,
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

/**
 * Live viewport width in CSS pixels. react-resizable-panels only accepts
 * percentage sizes, so pixel minimums have to be converted against this.
 */
export function useViewportWidth(): number {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const onChange = () => setWidth(window.innerWidth);
    onChange();
    window.addEventListener('resize', onChange);
    return () => window.removeEventListener('resize', onChange);
  }, []);

  return width;
}

export function useDebouncedCallback<A extends unknown[]>(fn: (...args: A) => void, delayMs: number) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const debounced = (...args: A) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      fnRef.current(...args);
    }, delayMs);
  };
  /** Drop a queued call. A reset must not be followed by a write of the layout it replaced. */
  debounced.cancel = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  return debounced;
}

/**
 * react-resizable-panels reports its layout on mount, so the shell's size
 * callbacks fire with values that are already current. Sizes are percentages the
 * library has already divided, so equality needs a rounding tolerance, not `===`.
 */
const SIZE_EPSILON = 0.01;

function sameLayout(a: WorkspaceLayout, b: WorkspaceLayout): boolean {
  if (a.symbol !== b.symbol || a.bottomTab !== b.bottomTab || a.range !== b.range) return false;
  for (const key of ['left', 'bottom', 'right', 'rightAI'] as const) {
    const pa = a.panels[key];
    const pb = b.panels[key];
    if (pa.collapsed !== pb.collapsed) return false;
    if (Math.abs(pa.size - pb.size) > SIZE_EPSILON) return false;
  }
  return true;
}

/**
 * A layout update carries only what changed. Panel groups report their sizes on
 * the same commit, so an update that rebuilt the whole `panels` object from a
 * render closure could revert a slot another group had just written — and the
 * watchlist then bounced between its stored size and its minimum forever.
 */
export type LayoutUpdate = Partial<Omit<WorkspaceLayout, 'panels'>> & {
  panels?: Partial<{
    [K in keyof WorkspaceLayout['panels']]: Partial<WorkspaceLayout['panels'][K]>;
  }>;
};

function mergeLayoutUpdate(prev: WorkspaceLayout, update: LayoutUpdate): WorkspaceLayout {
  const next: WorkspaceLayout = { ...prev, ...update, panels: { ...prev.panels } };
  for (const key of ['left', 'bottom', 'right', 'rightAI'] as const) {
    const patch = update.panels?.[key];
    if (patch) next.panels[key] = { ...next.panels[key], ...patch };
  }
  return next;
}

/**
 * Workspace layout persistence.
 *
 * `layout` holds the user's *stored intent* — the sizes they chose — and is the only
 * thing written to the cache and the server. What is actually painted can differ:
 * react-resizable-panels clamps a percentage against the current `minSize`, and a
 * short window therefore presents a bigger dock than the user stored. That derived
 * presentation must never be written back, or shrinking the window once would
 * permanently enlarge the stored dock (the ratchet this replaced). So the shell
 * reports sizes from `onLayout` only while the user is manipulating a handle, and
 * re-applies the stored intent once the constraint relaxes.
 */
export function useWorkspaceLayout(): {
  layout: WorkspaceLayout;
  setLayout: (update: LayoutUpdate) => void;
  resetPanelLayout: () => void;
  ready: boolean;
} {
  const [layout, setLayoutState] = useState<WorkspaceLayout>(DEFAULT_LAYOUT);
  const [ready, setReady] = useState(false);
  const current = useRef(layout);
  current.current = layout;
  // The stored layout arrives asynchronously. If the user has already changed
  // something in this mount, their change is the newer intent and the server
  // copy must not silently revert it (which also refetches the chart).
  const userEdited = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const cached = readCachedLayout();
    if (cached) setLayoutState(cached);
    (async () => {
      try {
        const data = await api.layouts.getWorkspace();
        if (!cancelled && !userEdited.current && data && data.layout) {
          setLayoutState(mergeLayout(data.layout));
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

  const setLayout = (update: LayoutUpdate) => {
    setLayoutState((prev) => {
      const next = mergeLayoutUpdate(prev, update);
      if (sameLayout(prev, next)) return prev;
      userEdited.current = true;
      persist(next);
      return next;
    });
  };

  /**
   * An explicit user action, so unlike a size report it always counts as intent.
   * Only the panel slots revert: the selected symbol, range and dock tab are view
   * state the reset button does not claim to touch.
   */
  const resetPanelLayout = () => {
    // A write of the layout being replaced must not land after this point.
    persist.cancel();
    const prev = current.current;
    const next: WorkspaceLayout = { ...prev, panels: mergeLayout(DEFAULT_LAYOUT).panels };
    // Also guards against a late GET restoring the layout this reset replaced.
    userEdited.current = true;
    if (sameLayout(prev, next)) return;
    setLayoutState(next);
    persist(next);
  };

  return { layout, setLayout, resetPanelLayout, ready };
}
