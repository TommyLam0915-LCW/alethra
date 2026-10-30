'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Panel, PanelGroup, PanelResizeHandle, type ImperativePanelHandle } from 'react-resizable-panels';
import { api, ApiError } from '@/lib/api';
import AppShell from '@/components/shell/AppShell';
import WorkspaceContextBar, { type ContextPortfolio } from '@/components/shell/WorkspaceContextBar';
import { useOperationalStatus } from '@/components/shell/useOperationalStatus';
import SymbolSearch from './SymbolSearch';
import PriceChart, { type ChartPeriod, type RangeKey } from './PriceChart';
import InstrumentHeader from './InstrumentHeader';
import WatchlistPanel from './WatchlistPanel';
import WatchlistDrawer from './WatchlistDrawer';
import ReplayController, { INTRADAY_TIMEFRAMES, type ReplaySession } from './ReplayController';
import DetailsPanel from './DetailsPanel';
import ActionPanel from './ActionPanel';
import AIPanel from './AIPanel';
import EvidenceDock from './EvidenceDock';
import type { PortfolioChoice } from './evidence';
import { useStrategyRules } from './strategyRules';
import { useMediaQuery, useViewportWidth, useWorkspaceLayout } from './hooks';
import { rangeToStart, todayStr, DEFAULT_LAYOUT, type BarsResponse, type InstrumentDetail, type Watchlist, type WorkspaceLayout } from './types';

/** Whole-day arithmetic on a YYYY-MM-DD date, in UTC so no DST drift. */
function shiftDate(date: string | null | undefined, days: number): string | null {
  if (!date) return null;
  const d = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function WorkspaceShell() {
  const { layout, setLayout, resetPanelLayout, ready } = useWorkspaceLayout();
  const isMobile = useMediaQuery('(max-width: 900px)');
  const isCompact = useMediaQuery('(max-width: 1279px)');
  const viewportWidth = useViewportWidth();
  const [authed, setAuthed] = useState(false);
  const [bars, setBars] = useState<BarsResponse | null>(null);
  const [barsLoading, setBarsLoading] = useState(false);
  const [barsError, setBarsError] = useState('');
  const [symbolInvalid, setSymbolInvalid] = useState(false);
  const [detail, setDetail] = useState<InstrumentDetail | null>(null);
  /**
   * The dataset window the user explicitly chose to view when their normal range
   * had no bars. A view choice for this mount only — it is not a stored range,
   * and it is dropped as soon as a range button is clicked or the symbol changes.
   */
  const [samplePeriod, setSamplePeriod] = useState<ChartPeriod | null>(null);
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [watchlistsLoading, setWatchlistsLoading] = useState(true);
  const [mobileDrawer, setMobileDrawer] = useState(false);
  const [portfolio, setPortfolio] = useState<ContextPortfolio | null>(null);
  /** Why the strip shows this portfolio — or why it shows none. Never a silent guess. */
  const [portfolioHint, setPortfolioHint] = useState<string | null>(null);
  /** What the order form has selected. When set, it outranks the auto-resolved account. */
  const [explicitPortfolio, setExplicitPortfolio] = useState<ContextPortfolio | null>(null);
  const handlePortfolioChange = useCallback((p: ContextPortfolio | null) => {
    setExplicitPortfolio(p);
  }, []);
  /**
   * Cancelling a pending order moves neither clock the dock refetches on — no new
   * bar, and no decision to bump the session's state_version — so the shell holds
   * the invalidation signal: ⑦ bumps it after a confirmed write, ⑧ reads it.
   */
  const [evidenceRevision, setEvidenceRevision] = useState(0);
  const handleBookChanged = useCallback(() => setEvidenceRevision((v) => v + 1), []);
  const [replaySession, setReplaySession] = useState<ReplaySession | null>(null);
  const [replayLoadError, setReplayLoadError] = useState('');
  /**
   * True once the resolved portfolio and its bound session have both settled.
   * Bars are requested only after that, because an unresolved workspace would ask
   * for the whole stored dataset and paint the future bars the replay cursor cuts.
   */
  const [replayReady, setReplayReady] = useState(false);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayActionError, setReplayActionError] = useState('');
  const { status, usageCount } = useOperationalStatus();

  const leftPanelRef = useRef<ImperativePanelHandle>(null);
  const bottomPanelRef = useRef<ImperativePanelHandle>(null);
  const rightPanelRef = useRef<ImperativePanelHandle>(null);
  const rightAIPanelRef = useRef<ImperativePanelHandle>(null);
  const watchlistToggleRef = useRef<HTMLButtonElement>(null);
  /** The gate's release path, shared with the handles' own `onBlur`. */
  const handleBlurRef = useRef<(() => void) | null>(null);
  /** Size reports caused by `applyIntent`, which is a repaint and never a user edit. */
  const suppressReportsRef = useRef(false);
  /** The centre column's vertical group, measured in px: react-resizable-panels takes
   * percentages only, so ⑧'s 240px floor has to be converted against a real height. */
  const bottomGroupRef = useRef<HTMLDivElement>(null);
  const [bottomGroupHeight, setBottomGroupHeight] = useState(0);
  /** ⑤'s chrome, measured from its intrinsic parts rather than as a difference against
   * the chart host: the host has its own CSS min-height, so a difference would shrink
   * and grow with the layout and feed back into the floors derived from it. */
  const topPanelRef = useRef<HTMLDivElement>(null);
  const [chromePx, setChromePx] = useState(0);

  useEffect(() => {
    const el = bottomGroupRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setBottomGroupHeight(entry.contentRect.height));
    observer.observe(el);
    return () => observer.disconnect();
  }, [isCompact, ready]);

  useEffect(() => {
    const panel = topPanelRef.current;
    if (!panel) return;
    const select = <T extends HTMLElement>(sel: string) => panel.querySelector<T>(sel);
    const measure = () => {
      const header = select<HTMLElement>('[data-testid="instrument-header"]');
      const replay = select<HTMLElement>('[data-testid="replay-controller"], .ws-replay');
      const host = select<HTMLElement>('[data-testid="price-chart"]');
      const paint = host?.querySelector<HTMLElement>('.ws-chart-canvas');
      // Everything in ⑤'s panel that is not the plotted area: ④ + ⑥ + the chart toolbar.
      const toolbar = host && paint ? Math.max(0, host.clientHeight - paint.clientHeight) : 0;
      setChromePx((header?.offsetHeight ?? 0) + (replay?.offsetHeight ?? 0) + toolbar);
    };
    const observer = new ResizeObserver(measure);
    for (const sel of [
      '[data-testid="instrument-header"]',
      '[data-testid="replay-controller"]',
      '.ws-replay',
      '[data-testid="price-chart"]',
      '[data-testid="price-chart"] .ws-chart-canvas',
    ]) {
      const el = select(sel);
      if (el) observer.observe(el);
    }
    measure();
    return () => observer.disconnect();
  }, [isCompact, ready, bars, replaySession]);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      window.location.href = '/login';
      return;
    }
    setAuthed(true);
  }, []);

  useEffect(() => {
    if (!isCompact) setMobileDrawer(false);
  }, [isCompact]);

  const symbol = layout.symbol;

  const loadWatchlists = useCallback(async () => {
    try {
      const data = await api.watchlists.list();
      setWatchlists(data.watchlists || []);
    } catch {
      setWatchlists([]);
    } finally {
      setWatchlistsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    loadWatchlists();
  }, [authed, loadWatchlists]);

  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    (async () => {
      setReplayReady(false);
      setReplaySession(null);
      setReplayLoadError('');
      try {
        const list = await api.paperPortfolio.list();
        if (cancelled) return;
        if (Array.isArray(list)) {
          // GET /paper-portfolio is filtered by owner_id server-side, so every
          // candidate here belongs to the signed-in user. Choosing is still not
          // the same as guessing: bind only when the choice is unambiguous.
          const bound = list.filter((p: any) => !!p.simulation_session_id);
          const chosen =
            list.length === 1 ? list[0] : bound.length === 1 ? bound[0] : null;
          if (chosen) {
            setPortfolio({
              id: chosen.id,
              currency: chosen.currency,
              current_virtual_cash: chosen.current_virtual_cash,
              status: chosen.status,
              simulation_session_id: chosen.simulation_session_id ?? null,
            });
            setPortfolioHint(
              bound.length === 1 && list.length > 1
                ? `${list.length} portfolios found; this one was chosen by the replay binding`
                : 'This is the only portfolio'
            );
          } else {
            // Several portfolios with no single replay binding: the workspace does
            // not pick one for the user, and the order form stays on explicit choice.
            setPortfolio(null);
            setPortfolioHint(
              list.length > 1
                ? `${list.length} portfolios found and the replay binding is ambiguous, so none was auto-selected — choose one in Manual paper order`
                : 'No paper portfolio created yet'
            );
          }
        }
      } catch {
        if (!cancelled) {
          setPortfolio(null);
          setPortfolioHint('Portfolio list could not be loaded');
        }
      } finally {
        // The account is settled; the session its binding names is resolved by the
        // effect below, which also re-runs when the user changes account later.
        if (!cancelled) setReplayReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authed]);

  // The one account this workspace speaks for: the order form's own pick wins, then
  // the unambiguous inference. Strip, chart cursor, ⑥, ⑦ and ⑧ all read this value.
  const contextPortfolio = explicitPortfolio ?? portfolio;
  const replaySessionId = contextPortfolio?.simulation_session_id ?? null;

  /** The session this effect loaded because an account named it — not one the user
   * started in the 策略 tab, which stays in use until the account changes. */
  const sessionFromBindingRef = useRef<string | null>(null);
  const replaySessionRef = useRef<ReplaySession | null>(null);
  useEffect(() => {
    replaySessionRef.current = replaySession;
  }, [replaySession]);

  /**
   * One binding, one cursor: the replay session is whichever session the account in
   * context names, so switching account moves the chart cut, the ⑥ controller and
   * the order form's settlement basis together. Keyed on the binding alone, so an
   * advance (same id) or a session created by the 策略 tab is never re-fetched over.
   */
  useEffect(() => {
    if (!authed) return;
    if (!replaySessionId) {
      setReplayLoadError('');
      if (sessionFromBindingRef.current) {
        sessionFromBindingRef.current = null;
        setReplaySession(null);
      }
      return;
    }
    if (replaySessionRef.current?.id === replaySessionId) {
      sessionFromBindingRef.current = replaySessionId;
      return;
    }
    let cancelled = false;
    setReplayReady(false);
    setReplayLoadError('');
    (async () => {
      try {
        const data = await api.simulations.get(replaySessionId);
        if (cancelled) return;
        sessionFromBindingRef.current = replaySessionId;
        setReplaySession(data);
      } catch (err) {
        if (!cancelled) {
          setReplayLoadError(err instanceof Error ? err.message : '回放工作階段無法載入');
        }
      } finally {
        if (!cancelled) setReplayReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authed, replaySessionId]);

  const replaySessionLoaded = !!replaySession;

  const replayIsIntraday =
    !!replaySession?.timeframe && INTRADAY_TIMEFRAMES.has(replaySession.timeframe);
  const replayClock = replaySession
    ? (replayIsIntraday
        ? replaySession.current_bar_timestamp?.slice(0, 16).replace('T', ' ')
        : replaySession.current_snapshot_date) ?? null
    : null;
  // The chart series, its indicators and the header summary are all derived from
  // this request window, so clipping the request is what keeps future bars out of
  // every one of them. An intraday clock stops at the last completed day because
  // /bars only serves 1d — the in-progress day would carry post-cursor minutes.
  const replayBarsEnd = replaySession
    ? replayIsIntraday
      ? shiftDate(replaySession.current_bar_timestamp?.slice(0, 10), -1)
      : replaySession.current_snapshot_date
    : null;

  // The session response names no instrument; only its locked strategy version does.
  const strategyRules = useStrategyRules(replaySession);
  const sessionInstrument =
    replaySession &&
    strategyRules.status === 'resolved' &&
    strategyRules.versionId === replaySession.experiment_version_id
      ? strategyRules.assetIds[0] ?? null
      : null;

  /**
   * A replay cursor is a statement about one price series. When the session is
   * known to trade a different instrument than the plotted symbol, its cursor is
   * not this chart's cutoff — clipping 「US:AAPL」 candles at another instrument's
   * clock would sell a replay view the session never produced. Rules that could
   * not be resolved keep the existing cut, because nothing is claimed about them.
   */
  const replayInstrumentMismatch = !!sessionInstrument && sessionInstrument !== symbol;
  const chartBarsEnd = replayInstrumentMismatch ? null : replayBarsEnd;
  const replayInstrumentNote = replayInstrumentMismatch
    ? `This session replays ${sessionInstrument}, not the charted ${symbol}; the chart is not cut to this cursor, and orders assume the session instrument.`
    : null;

  const runReplayAction = async (action: 'advance' | 'pause' | 'resume') => {
    if (!replaySession) return;
    setReplayBusy(true);
    setReplayActionError('');
    try {
      const call =
        action === 'advance'
          ? api.simulations.advance(replaySession.id)
          : action === 'pause'
            ? api.simulations.pause(replaySession.id)
            : api.simulations.resume(replaySession.id);
      const res = await call;
      setReplaySession(res?.session ?? res);
    } catch (err) {
      setReplayActionError(err instanceof Error ? err.message : '回放操作失敗');
    } finally {
      setReplayBusy(false);
    }
  };

  // Only a provider-declared window may be offered as a sample. Cached rows are
  // not proof of what exists, and nothing here guesses a period.
  const coverage = detail?.data_coverage ?? null;
  const availablePeriod: ChartPeriod | null =
    coverage &&
    coverage.availability_source === 'provider_declared' &&
    coverage.available_start &&
    coverage.available_end
      ? { start: coverage.available_start, end: coverage.available_end }
      : null;

  useEffect(() => {
    if (!authed || !replayReady) return;
    // A portfolio bound to a session whose cursor could not be read has no trustworthy
    // cut. Requesting the full stored window would paint the bars the replay hides, so
    // the chart stays unavailable until the cursor is known.
    if (replaySessionId && !replaySessionLoaded) {
      setBars(null);
      setBarsLoading(false);
      setBarsError('回放游標未能載入：取得游標前不請求歷史數據，避免顯示未來 K 線');
      return;
    }
    // The replay cursor is a hard cut: it outranks a sample period, which may
    // extend past it — but only when it is this series' own cursor.
    const reqWindow = chartBarsEnd
      ? { start: rangeToStart(layout.range, chartBarsEnd), end: chartBarsEnd }
      : samplePeriod ?? { start: rangeToStart(layout.range), end: todayStr() };
    let cancelled = false;
    setBarsLoading(true);
    setBarsError('');
    setSymbolInvalid(false);
    (async () => {
      try {
        const data = await api.instruments.bars(symbol, reqWindow.start, reqWindow.end);
        if (!data || !Array.isArray(data.bars)) {
          throw new Error('無效的數據回應');
        }
        if (!cancelled) setBars(data);
      } catch (err) {
        if (!cancelled) {
          setBars(null);
          setBarsError(err instanceof Error ? err.message : '無法載入數據');
          setSymbolInvalid(err instanceof ApiError && err.status === 404);
        }
      } finally {
        if (!cancelled) setBarsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    authed,
    symbol,
    layout.range,
    samplePeriod,
    chartBarsEnd,
    replayReady,
    replaySessionId,
    replaySessionLoaded,
  ]);

  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    setDetail(null);
    (async () => {
      try {
        const data = await api.instruments.get(symbol);
        if (!cancelled) setDetail(data);
      } catch {
        // details panel shows its own error
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authed, symbol]);

  const onSelectSymbol = (instrumentId: string) => {
    // A period chosen for the old symbol says nothing about the new one.
    setSamplePeriod(null);
    setSymbolInvalid(false);
    setLayout({ symbol: instrumentId });
    setMobileDrawer(false);
  };

  const recoverFromInvalidSymbol = () => {
    setSamplePeriod(null);
    setSymbolInvalid(false);
    setLayout({ symbol: DEFAULT_LAYOUT.symbol });
  };

  const addCurrentToWatchlist = async () => {
    let wls = watchlists;
    if (wls.length === 0) {
      const created = await api.watchlists.create();
      wls = [created.watchlist];
      setWatchlists(wls);
    }
    const target = wls[0];
    await api.watchlists.addItem(target.id, symbol);
    await loadWatchlists();
  };

  const removeWatchlistItem = async (watchlistId: string, itemSymbol: string) => {
    const data = await api.watchlists.removeItem(watchlistId, itemSymbol);
    setWatchlists((prev) => prev.map((w) => (w.id === watchlistId ? data.watchlist : w)));
  };

  const moveWatchlistItem = async (watchlistId: string, itemSymbol: string, direction: -1 | 1) => {
    const wl = watchlists.find((w) => w.id === watchlistId);
    if (!wl) return;
    const symbols = wl.items.map((i) => i.symbol);
    const idx = symbols.indexOf(itemSymbol);
    const target = idx + direction;
    if (idx < 0 || target < 0 || target >= symbols.length) return;
    [symbols[idx], symbols[target]] = [symbols[target], symbols[idx]];
    const data = await api.watchlists.reorder(watchlistId, symbols);
    setWatchlists((prev) => prev.map((w) => (w.id === watchlistId ? data.watchlist : w)));
  };

  const deleteWatchlist = async (watchlistId: string) => {
    await api.watchlists.remove(watchlistId);
    setWatchlists((prev) => prev.filter((w) => w.id !== watchlistId));
  };

  const createWatchlist = async () => {
    await api.watchlists.create();
    await loadWatchlists();
  };

  const togglePanel = (key: 'left' | 'bottom' | 'right') => {
    const collapsed = !layout.panels[key].collapsed;
    setLayout({ panels: { [key]: { collapsed } } });
    const ref = key === 'left' ? leftPanelRef : key === 'bottom' ? bottomPanelRef : rightPanelRef;
    if (ref.current) {
      if (collapsed) ref.current.collapse();
      else ref.current.expand();
    }
  };

  // Panel sizes are percentages of the group's content box, which already excludes
  // the 4px resize handles — so a handle is added back before converting a px floor.
  // The instrument code must stay readable however wide the window is.
  const RESIZE_HANDLE_PX = 8;
  const watchlistMinSize =
    viewportWidth > 0
      ? Math.max(8, Math.min(25, ((224 + RESIZE_HANDLE_PX) / viewportWidth) * 100))
      : 8;
  // ⑦ has a 304px design floor, but only from the three-column breakpoint up: at
  // 1024–1279 the same floor would take a third of the window away from the chart.
  const actionMinSize =
    viewportWidth >= 1280
      ? Math.max(10, Math.min(30, ((304 + RESIZE_HANDLE_PX) / viewportWidth) * 100))
      : 10;

  /**
   * ⑤ and ⑧'s floors, converted against the measured centre column.
   *
   * ⑤'s 320px is the plotted canvas, not its panel frame, so the row it needs is the
   * chrome measured inside its panel (④ header + ⑥ replay strip + the chart toolbar)
   * plus the canvas target. ⑧'s design height is 240–300px, and the height it can never
   * give up is its own interactive chrome — panel header 40 + tab strip 34 + scope filter
   * row 46 = 120px, measured on the live runtime — because below that the Panel's
   * `overflow: hidden` puts the tabs outside the band entirely.
   *
   * When a column cannot hold even ⑤'s CSS canvas minimum next to ⑧'s chrome (a 620px
   * window), the two minimums are mutually impossible. Rather than lock them against
   * each other — which is what snapped ⑧ shut and clipped ⑤'s plot — the column stops
   * compressing and scrolls: `columnPx` becomes the group's own minimum height and the
   * centre panel its scroll container, so both floors are real pixels and every control
   * stays reachable, by scroll where the window is too short for it to be visible.
   *
   * The roomy pair is also out of reach at 1440×900, not only on short windows: the centre
   * column measures 776px there while 242 + 320 + 8 + 240 needs 810px, so the compact
   * floors apply and the canvas lands at 295.6px. ⑥'s 129px strip is what the pair lacks.
   */
  const PAINT_MIN_PX = 320;
  const PAINT_COMPACT_PX = 200;
  const EVIDENCE_MIN_PX = 240;
  const EVIDENCE_COMPACT_PX = 160;
  const EVIDENCE_ESSENTIAL_PX = 120;
  /** A couple of pixels of slack, so the two converted minimums sum to just under 100%. */
  const CRAMPED_SLACK_PX = 6;
  const groupHeightPx = bottomGroupHeight;
  const roomy = groupHeightPx - (chromePx + PAINT_MIN_PX) - RESIZE_HANDLE_PX >= EVIDENCE_MIN_PX;
  const paintFloorPx = roomy ? PAINT_MIN_PX : PAINT_COMPACT_PX;
  const chartFloorPx = chromePx + paintFloorPx;
  const dockRoomPx = groupHeightPx - chartFloorPx - RESIZE_HANDLE_PX;
  const cramped = groupHeightPx > 0 && dockRoomPx < EVIDENCE_ESSENTIAL_PX;
  const dockDesignPx = roomy ? EVIDENCE_MIN_PX : EVIDENCE_COMPACT_PX;
  const dockFloorPx = cramped
    ? EVIDENCE_ESSENTIAL_PX
    : Math.min(dockDesignPx, Math.max(dockRoomPx, EVIDENCE_ESSENTIAL_PX));
  const columnPx = cramped
    ? chartFloorPx + EVIDENCE_ESSENTIAL_PX + RESIZE_HANDLE_PX + CRAMPED_SLACK_PX
    : groupHeightPx;
  const toPct = (px: number) => (columnPx > 0 ? ((px + RESIZE_HANDLE_PX / 2) / columnPx) * 100 : 0);
  const handlePct = columnPx > 0 ? ((RESIZE_HANDLE_PX / 2) / columnPx) * 100 : 0;
  const clampPct = (value: number, min: number, max: number) =>
    Math.max(min, Math.min(Math.max(max, min), value));
  const evidenceMinSize = columnPx > 0 ? clampPct(toPct(dockFloorPx), 4, 60) : 4;
  const chartMinSize =
    columnPx > 0 ? clampPct(toPct(chartFloorPx), 4, 100 - evidenceMinSize - handlePct) : 20;

  /**
   * Only a handle the user is actually manipulating turns a size report into stored
   * intent. The panel group also reports on mount, on hydration, after a viewport
   * clamp and after a programmatic collapse — and treating those as edits is what let
   * one short window permanently enlarge the stored dock (a presentation clamp being
   * saved as a preference).
   */
  const resizingRef = useRef(false);
  useEffect(() => {
    // The library installs its own pointerdown on the handle after spreading the rest
    // props, so a gesture cannot be detected with a prop — it has to be observed on
    // the way in, before the handle's own handler runs.
    //
    // The match is our own `data-ws-handle` marker, not `[role="separator"]`: a splitter
    // that persists nothing (⑦'s inner 詳情/操作 divider) would otherwise open this gate too,
    // and any clamp report that arrived while the flag was still up would be stored.
    const onPointerDown = (event: PointerEvent) => {
      if ((event.target as HTMLElement | null)?.closest?.('[data-ws-handle]')) {
        resizingRef.current = true;
      }
    };
    // A focused handle is resized with Arrow/Home/End. The library binds its own keydown
    // to the handle element (verified in the installed 2.1.7 build), so a capture listener
    // sees the same keys first and the keyboard path is intent exactly like a drag.
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.closest?.('[data-ws-handle]') &&
        (event.key.startsWith('Arrow') || event.key === 'Home' || event.key === 'End')
      ) {
        resizingRef.current = true;
      }
    };
    const stop = () => {
      // The library's own pointerup handler runs in the same dispatch and may emit
      // the drag's final layout report, so the flag must outlive it by one task.
      setTimeout(() => {
        resizingRef.current = false;
      }, 0);
    };
    const onVisibilityChange = () => {
      if (document.hidden) stop();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    window.addEventListener('keyup', stop);
    // Every way a gesture can end without a matching release event. Without these the
    // flag could stay up after an alt-tab or a focus jump, and the next mount/clamp
    // report would then be persisted as if the user had made it.
    window.addEventListener('blur', stop);
    document.addEventListener('visibilitychange', onVisibilityChange);
    handleBlurRef.current = stop;
    return () => {
      resizingRef.current = false;
      handleBlurRef.current = null;
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      window.removeEventListener('keyup', stop);
      window.removeEventListener('blur', stop);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, []);

  /**
   * A size report becomes stored intent only while the user drives one of this shell's
   * handles, and never for a repaint `applyIntent` pushes into the panels: a drag can flip
   * the cramped/roomy regime, which runs that repaint in the same task group, and saving
   * its clamped result is the ratchet re-entering through a different door.
   */
  const isUserReport = () => resizingRef.current && !suppressReportsRef.current;

  const persistHorizontalSizes = (sizes: number[]) => {
    if (!isUserReport()) return;
    if (sizes.length === 3) {
      setLayout({
        panels: {
          left: { size: sizes[0] },
          right: { size: sizes[2] },
        },
      });
    } else if (sizes.length === 2) {
      // Below 1280px the watchlist column collapses into a drawer, so the
      // remaining panels are centre + right.
      setLayout({
        panels: { right: { size: sizes[1] } },
      });
    }
  };

  const persistBottomSize = (sizes: number[]) => {
    if (!isUserReport()) return;
    if (sizes.length === 2) {
      setLayout({
        panels: { bottom: { size: sizes[1] } },
      });
    }
  };

  const persistRightAISize = (sizes: number[]) => {
    if (!isUserReport()) return;
    if (sizes.length === 2) {
      setLayout({
        panels: { rightAI: { size: sizes[0] } },
      });
    }
  };

  /**
   * Collapsed state is intent only when the user caused it. The library fires
   * `onCollapse`/`onExpand` whenever the *rendered* size crosses `collapsedSize`, so a
   * viewport clamp that snaps a collapsible panel shut reports through the same
   * callback — and persisting it would store a presentation as a preference. The toggle
   * buttons write the flag themselves before calling `collapse()`/`expand()`, so the
   * callbacks carry nothing but drag-past-the-minimum, which is a gesture. The same
   * repaint suppression applies: `expand()` inside `applyIntent` reports an expansion
   * through this callback too.
   */
  const recordCollapseState = (
    key: 'left' | 'bottom' | 'right' | 'rightAI',
    collapsed: boolean
  ) => {
    if (!isUserReport()) return;
    setLayout({ panels: { [key]: { collapsed } } });
  };

  /**
   * Push stored intent into the live panels. `resize()` alone is not enough: it does not
   * open a panel the library has collapsed, so a clamp that snapped a panel shut would
   * outlive the short window that caused it. `expand()` is a no-op when the panel is
   * already open.
   *
   * The reports this repaint produces are suppressed by construction, not left to the
   * gesture flag happening to be closed: a real drag can flip the sizing regime and run
   * this in the same task group, and persisting a clamped number as the user's preference
   * is exactly the defect this replaced.
   */
  const applyIntent = (panels: WorkspaceLayout['panels']) => {
    suppressReportsRef.current = true;
    const apply = (
      ref: { current: ImperativePanelHandle | null },
      slot: { size: number; collapsed: boolean }
    ) => {
      const el = ref.current;
      if (!el) return;
      if (slot.collapsed) {
        el.collapse();
        return;
      }
      el.expand();
      el.resize(slot.size);
    };
    apply(leftPanelRef, panels.left);
    apply(bottomPanelRef, panels.bottom);
    apply(rightPanelRef, panels.right);
    apply(rightAIPanelRef, panels.rightAI);
    requestAnimationFrame(() => {
      suppressReportsRef.current = false;
    });
  };

  /**
   * A cramped group is deliberately taller than the window, so ⑧'s stored percentage
   * buys more pixels than the column can spend while still giving ⑤ its canvas floor.
   * Cap the dock at what leaves ⑤ that floor — the rest of its content scrolls inside
   * `.ws-panel-body`, whereas the plotted area cannot scroll back into existence.
   */
  const fitToChartFloor = (panels: WorkspaceLayout['panels']) => {
    if (!cramped) return panels;
    const dockMaxPct = 100 - chartMinSize - handlePct;
    return panels.bottom.size > dockMaxPct
      ? { ...panels, bottom: { ...panels.bottom, size: dockMaxPct } }
      : panels;
  };

  // A clamp that was needed while the window was short is not the user's stored size.
  // Once the floors relax again, put the intent back — including the panels a clamp
  // snapped shut, which stay shut on their own; the library reads a collapse as sticky
  // until something expands it. The resulting size reports are not gestures, so this
  // cannot write itself into the layout.
  //
  // `ready` is a dependency because the panels take uncontrolled `defaultSize`: an intent
  // fetched after mount is otherwise never painted, and the store and the screen silently
  // disagree. The hook already refuses to hydrate over an edit the user made in this mount.
  const floorKey = `${watchlistMinSize.toFixed(4)}|${actionMinSize.toFixed(
    4
  )}|${evidenceMinSize.toFixed(4)}|${chartMinSize.toFixed(4)}`;
  useEffect(() => {
    if (!ready || isCompact) return;
    applyIntent(fitToChartFloor(layout.panels));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [floorKey, ready]);

  const toggleAIPanel = () => {
    const collapsed = !layout.panels.rightAI.collapsed;
    setLayout({ panels: { rightAI: { collapsed } } });
    if (rightAIPanelRef.current) {
      if (collapsed) rightAIPanelRef.current.collapse();
      else rightAIPanelRef.current.expand();
    }
  };

  /**
   * 回復預設版面. The stored intent goes back to the design defaults and the live
   * panels follow in the same action — the library reads `defaultSize` only on mount,
   * so without the imperative resize the button would change the record but not the
   * room. The resize reports sizes without a gesture, so it cannot re-persist itself.
   */
  const onResetPanelLayout = () => {
    resetPanelLayout();
    applyIntent(fitToChartFloor(DEFAULT_LAYOUT.panels));
  };

  if (!authed || !ready) {
    return (
      <AppShell active="workspace" fullHeight>
        <div style={{ margin: 'auto' }} className="ws-muted">
          載入工作台…
        </div>
      </AppShell>
    );
  }

  const renderWatchlist = (extraActions?: ReactNode, className?: string) => (
    <WatchlistPanel
      watchlists={watchlists}
      currentSymbol={symbol}
      loading={watchlistsLoading}
      onSelectSymbol={onSelectSymbol}
      onAddCurrent={addCurrentToWatchlist}
      onRemoveItem={removeWatchlistItem}
      onMoveItem={moveWatchlistItem}
      onCreate={createWatchlist}
      onDelete={deleteWatchlist}
      extraActions={extraActions}
      className={className}
    />
  );

  const drawerCloseButton = (
    <button
      className="ws-icon-btn"
      title="收合觀察列表"
      aria-label="收合觀察列表"
      onClick={() => setMobileDrawer(false)}
      data-testid="watchlist-drawer-close"
    >
      ✕
    </button>
  );

  const chartBlock = (
    <>
      <InstrumentHeader data={bars ?? detail} loading={barsLoading} coverage={coverage} />
      {barsError ? (
        <div className="ws-message-error" style={{ margin: 10 }} data-testid="workspace-bars-error">
          <div>
            {symbolInvalid
              ? `已儲存的標的「${symbol}」不存在，無法載入數據。`
              : barsError}
          </div>
          {symbolInvalid && (
            <div
              data-testid="invalid-symbol-recovery"
              style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}
            >
              <button
                type="button"
                onClick={recoverFromInvalidSymbol}
                style={{
                  padding: '4px 10px',
                  border: '1px solid currentColor',
                  borderRadius: 4,
                  background: 'transparent',
                  color: 'inherit',
                  cursor: 'pointer',
                  fontSize: 12,
                }}
              >
                改用預設標的（{DEFAULT_LAYOUT.symbol}）
              </button>
              <span style={{ fontSize: 12 }}>或在搜尋欄選擇其他標的</span>
            </div>
          )}
        </div>
      ) : (
        <div className="ws-panel" style={{ flex: 1, minHeight: 0, position: 'relative' }}>
          <PriceChart
            data={bars}
            loading={barsLoading}
            range={layout.range}
            onRangeChange={(r: RangeKey) => {
              setSamplePeriod(null);
              setLayout({ range: r });
            }}
            availablePeriod={availablePeriod}
            samplePeriodActive={samplePeriod !== null}
            onUseSamplePeriod={() => setSamplePeriod(availablePeriod)}
          />
        </div>
      )}
    </>
  );

  const replayBlock = replaySession ? (
    <ReplayController
      session={replaySession}
      barsEnd={chartBarsEnd}
      instrumentNote={replayInstrumentNote}
      busy={replayBusy}
      error={replayActionError}
      onAdvance={() => runReplayAction('advance')}
      onPause={() => runReplayAction('pause')}
      onResume={() => runReplayAction('resume')}
    />
  ) : replayLoadError ? (
    <div className="ws-replay" data-testid="replay-load-error">
      <span className="ws-replay-badge">歷史回放</span>
      <span className="ws-replay-error" role="alert">
        {replayLoadError}
      </span>
    </div>
  ) : null;

  // The dock cannot be allowed to resolve its own book: one screen would then name
  // two portfolios. It reports evidence for whatever the form currently speaks for,
  // retired or completed included — read-only, since those reject simulated orders.
  const chosenPortfolio: PortfolioChoice | null = explicitPortfolio
    ? {
        id: explicitPortfolio.id,
        reason:
          `與「手動模擬單」目前所選組合一致（${
            explicitPortfolio.status === 'active'
              ? '狀態為進行中；實際能否送單以該面板的提交目標為準'
              : '僅唯讀檢視，此狀態送單會被後端拒絕'
          }）` +
          (replaySession && explicitPortfolio.simulation_session_id !== replaySession.id
            ? `；其回放綁定與目前工作階段 ${replaySession.id.slice(0, 8)} 不同步，圖表游標仍跟隨工作階段`
            : ''),
      }
    : null;

  const evidenceDock = (
    <EvidenceDock
      symbol={symbol}
      session={replaySession}
      chosen={chosenPortfolio}
      revision={evidenceRevision}
      tab={layout.bottomTab}
      onTabChange={(tab) => setLayout({ bottomTab: tab })}
      hideHeader
    />
  );

  // ⑦ must be given a definite height: it fills its slot with `flex: 1 1 0%`, so in
  // an auto-height column next to the details table it collapsed to zero and every
  // control inside it became unreachable. `.ws-panel-body` scrolls, so a bounded box
  // is all it needs.
  const actionPanel = (
    <ActionPanel
      instrument={detail}
      bars={bars}
      instrumentId={symbol}
      session={replaySession}
      rules={strategyRules}
      onSessionChange={setReplaySession}
      onPortfolioChange={handlePortfolioChange}
      onBookChanged={handleBookChanged}
    />
  );

  // The strip, the chart cursor and the dock all read the single binding derived
  // above; a pick whose session has not finished loading is shown as a conflict
  // instead of being swapped away.
  const chosenBound = explicitPortfolio?.simulation_session_id ?? null;
  const contextPortfolioHint =
    explicitPortfolio && chosenBound !== (replaySession?.id ?? null)
      ? `已在「手動模擬單」選擇組合 ${explicitPortfolio.id.slice(0, 8)}，其回放綁定（${
          chosenBound ? chosenBound.slice(0, 8) : '未綁定'
        }）與目前工作階段（${
          replaySession ? replaySession.id.slice(0, 8) : '未載入'
        }）不同步：圖表游標仍跟隨目前工作階段，送單前請確認組合與成交基準。`
      : portfolioHint;

  const contextBar = (
    <div className="ws-contextbar">
      <WorkspaceContextBar
        symbol={symbol}
        name={detail?.name ?? bars?.name ?? null}
        portfolio={contextPortfolio}
        portfolioHint={contextPortfolioHint}
        meta={bars ?? detail}
        status={status}
        usageCount={usageCount}
        replayTimestamp={replaySession ? replayClock ?? '尚未推進' : null}
        replayInstrumentNote={replayInstrumentNote}
      />
      <div className="ws-contextbar-tools">
        <button
          className="ws-text-btn"
          type="button"
          title="只重設面板寬度與高度，不影響投資組合、回放 session 或訂單"
          aria-label="回復預設版面"
          onClick={onResetPanelLayout}
          data-testid="reset-layout"
        >
          回復預設版面
        </button>
        <button
          ref={watchlistToggleRef}
          className="ws-icon-btn ws-watchlist-toggle"
          title="觀察列表"
          aria-label={mobileDrawer ? '收合觀察列表' : '展開觀察列表'}
          aria-expanded={mobileDrawer}
          aria-controls={mobileDrawer ? 'watchlist-drawer' : undefined}
          onClick={() => setMobileDrawer((v) => !v)}
          data-testid="mobile-watchlist-toggle"
        >
          ☰
        </button>
        <SymbolSearch onSelect={onSelectSymbol} />
      </div>
    </div>
  );

  return (
    <AppShell active="workspace" fullHeight contextBar={contextBar}>

      {isMobile ? (
        <div className="ws-main" data-testid="workspace-mobile">
          {mobileDrawer && (
            <WatchlistDrawer
              onClose={() => setMobileDrawer(false)}
              returnFocusRef={watchlistToggleRef}
            >
              {renderWatchlist(drawerCloseButton)}
            </WatchlistDrawer>
          )}
          <div style={{ height: 480, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
            {chartBlock}
          </div>
          {replayBlock}
          <div style={{ height: 420, flexShrink: 0 }}>
            <AIPanel currentSymbol={symbol} />
          </div>
          <DetailsPanel instrumentId={symbol} />
          <div style={{ height: 6, background: '#131722', flexShrink: 0 }} />
          <div style={{ height: 480, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
            {actionPanel}
          </div>
          <div
            data-testid="bottom-tabs"
            style={{ height: 480, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
          >
            <div className="ws-panel-header">交易證據</div>
            {evidenceDock}
          </div>
        </div>
      ) : (
        <div className="ws-main" style={{ display: 'flex', flexDirection: 'column' }}>
          {isCompact && mobileDrawer && (
            <WatchlistDrawer
              onClose={() => setMobileDrawer(false)}
              returnFocusRef={watchlistToggleRef}
            >
              {renderWatchlist(drawerCloseButton)}
            </WatchlistDrawer>
          )}
          <div style={{ flex: 1, minHeight: 0 }}>
            <PanelGroup direction="horizontal" onLayout={persistHorizontalSizes}>
              {!isCompact && (
                <Panel
                  defaultSize={layout.panels.left.size}
                  minSize={watchlistMinSize}
                  collapsible
                  ref={leftPanelRef}
                  onCollapse={() => recordCollapseState('left', true)}
                  onExpand={() => recordCollapseState('left', false)}
                >
                  <div style={{ display: 'flex', height: '100%' }}>
                    {renderWatchlist(
                      <button
                        className="ws-icon-btn"
                        title="收合"
                        aria-label="收合觀察列表"
                        onClick={() => togglePanel('left')}
                      >
                        ⟨
                      </button>,
                      'ws-panel-fill'
                    )}
                  </div>
                </Panel>
              )}
              {!isCompact && (
                <PanelResizeHandle
                  className="ws-desktop-only"
                  data-ws-handle="left"
                  onBlur={() => handleBlurRef.current?.()}
                  style={{ width: 4, background: '#131722', cursor: 'col-resize' }}
                />
              )}
              <Panel minSize={30}>
                {/* In a short column the two minimums cannot both fit the viewport, so the
                    column scrolls instead of letting the Panel clip them. */}
                <div
                  ref={bottomGroupRef}
                  style={{ height: '100%', overflowY: cramped ? 'auto' : undefined }}
                >
                  <PanelGroup
                    direction="vertical"
                    onLayout={persistBottomSize}
                    style={cramped ? { minHeight: columnPx } : undefined}
                  >
                    <Panel minSize={chartMinSize}>
                      <div
                        ref={topPanelRef}
                        style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
                      >
                        {chartBlock}
                        {replayBlock}
                      </div>
                    </Panel>
                    <PanelResizeHandle
                      data-ws-handle="dock"
                      onBlur={() => handleBlurRef.current?.()}
                      style={{ height: 4, background: '#131722', cursor: 'row-resize' }}
                    />
                    <Panel
                      defaultSize={layout.panels.bottom.size}
                      minSize={evidenceMinSize}
                      collapsible
                      ref={bottomPanelRef}
                      onCollapse={() => recordCollapseState('bottom', true)}
                      onExpand={() => recordCollapseState('bottom', false)}
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                        <div className="ws-panel-header">
                          交易證據
                          <div className="ws-panel-header-actions">
                            <button className="ws-icon-btn" title="收合" aria-label="收合交易證據" onClick={() => togglePanel('bottom')}>
                              ⌄
                            </button>
                          </div>
                        </div>
                        {/* Must be a flex column: the dock fills it with .ws-panel-fill, which is
                            only honoured inside a flex container. As a plain block the dock grew to
                            its content height and the Panel's overflow:hidden clipped its controls. */}
                        <div
                          data-testid="bottom-tabs"
                          style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
                        >
                          {evidenceDock}
                        </div>
                      </div>
                    </Panel>
                  </PanelGroup>
                </div>
              </Panel>
              <PanelResizeHandle
                data-ws-handle="action"
                onBlur={() => handleBlurRef.current?.()}
                style={{ width: 4, background: '#131722', cursor: 'col-resize' }}
              />
              <Panel
                defaultSize={layout.panels.right.size}
                minSize={actionMinSize}
                collapsible
                ref={rightPanelRef}
                onCollapse={() => recordCollapseState('right', true)}
                onExpand={() => recordCollapseState('right', false)}
              >
                <PanelGroup direction="vertical" onLayout={persistRightAISize}>
                  <Panel
                    defaultSize={layout.panels.rightAI.size}
                    minSize={10}
                    collapsible
                    ref={rightAIPanelRef}
                    onCollapse={() => recordCollapseState('rightAI', true)}
                    onExpand={() => recordCollapseState('rightAI', false)}
                  >
                    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                      <div className="ws-panel-header">
                        AI 助手
                        <div className="ws-panel-header-actions">
                          <button className="ws-icon-btn" title="收合 AI 面板" aria-label="收合 AI 面板" onClick={toggleAIPanel}>
                            ⌃
                          </button>
                        </div>
                      </div>
                      <div style={{ flex: 1, minHeight: 0 }}>
                        <AIPanel currentSymbol={symbol} />
                      </div>
                    </div>
                  </Panel>
                  <PanelResizeHandle
                    data-ws-handle="ai"
                    onBlur={() => handleBlurRef.current?.()}
                    style={{ height: 4, background: '#131722', cursor: 'row-resize' }}
                  />
                  <Panel minSize={10}>
                    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                      <div className="ws-panel-header">
                        詳情 / 操作
                        <div className="ws-panel-header-actions">
                          <button className="ws-icon-btn" title="收合" aria-label="收合詳情與操作" onClick={() => togglePanel('right')}>
                            ⟩
                          </button>
                        </div>
                      </div>
                      <div style={{ flex: 1, minHeight: 0 }}>
                        <PanelGroup direction="vertical">
                          <Panel defaultSize={22} minSize={10}>
                            <div
                              style={{
                                display: 'flex',
                                flexDirection: 'column',
                                height: '100%',
                              }}
                            >
                              <DetailsPanel instrumentId={symbol} />
                            </div>
                          </Panel>
                          <PanelResizeHandle style={{ height: 4, background: '#131722', cursor: 'row-resize' }} />
                          <Panel defaultSize={78} minSize={50}>
                            <div
                              style={{
                                display: 'flex',
                                flexDirection: 'column',
                                height: '100%',
                              }}
                            >
                              {actionPanel}
                            </div>
                          </Panel>
                        </PanelGroup>
                      </div>
                    </div>
                  </Panel>
                </PanelGroup>
              </Panel>
            </PanelGroup>
          </div>
        </div>
      )}
    </AppShell>
  );
}

export type { WorkspaceLayout };
