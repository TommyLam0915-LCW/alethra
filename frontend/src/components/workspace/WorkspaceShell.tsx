'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Panel, PanelGroup, PanelResizeHandle, type ImperativePanelHandle } from 'react-resizable-panels';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';
import SymbolSearch from './SymbolSearch';
import StatusChips from './StatusChips';
import PriceChart, { type RangeKey } from './PriceChart';
import WatchlistPanel from './WatchlistPanel';
import DetailsPanel from './DetailsPanel';
import OrderPreviewPanel from './OrderPreviewPanel';
import AIPanel from './AIPanel';
import BottomTabs from './BottomTabs';
import { useMediaQuery, useWorkspaceLayout } from './hooks';
import { rangeToStart, todayStr, type BarsResponse, type InstrumentDetail, type Watchlist, type WorkspaceLayout } from './types';

export default function WorkspaceShell() {
  const { layout, setLayout, ready } = useWorkspaceLayout();
  const isMobile = useMediaQuery('(max-width: 900px)');
  const [authed, setAuthed] = useState(false);
  const [bars, setBars] = useState<BarsResponse | null>(null);
  const [barsLoading, setBarsLoading] = useState(false);
  const [barsError, setBarsError] = useState('');
  const [detail, setDetail] = useState<InstrumentDetail | null>(null);
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [watchlistsLoading, setWatchlistsLoading] = useState(true);
  const [mobileDrawer, setMobileDrawer] = useState(false);

  const leftPanelRef = useRef<ImperativePanelHandle>(null);
  const bottomPanelRef = useRef<ImperativePanelHandle>(null);
  const rightPanelRef = useRef<ImperativePanelHandle>(null);
  const rightAIPanelRef = useRef<ImperativePanelHandle>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      window.location.href = '/login';
      return;
    }
    setAuthed(true);
  }, []);

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
    setBarsLoading(true);
    setBarsError('');
    (async () => {
      try {
        const data = await api.instruments.bars(symbol, rangeToStart(layout.range), todayStr());
        if (!data || !Array.isArray(data.bars)) {
          throw new Error('無效的數據回應');
        }
        if (!cancelled) setBars(data);
      } catch (err) {
        if (!cancelled) {
          setBars(null);
          setBarsError(err instanceof Error ? err.message : '無法載入數據');
        }
      } finally {
        if (!cancelled) setBarsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authed, symbol, layout.range]);

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
    setLayout({ symbol: instrumentId });
    setMobileDrawer(false);
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
    setLayout({ panels: { ...layout.panels, [key]: { ...layout.panels[key], collapsed } } });
    const ref = key === 'left' ? leftPanelRef : key === 'bottom' ? bottomPanelRef : rightPanelRef;
    if (ref.current) {
      if (collapsed) ref.current.collapse();
      else ref.current.expand();
    }
  };

  const persistHorizontalSizes = (sizes: number[]) => {
    if (sizes.length === 3) {
      setLayout({
        panels: {
          ...layout.panels,
          left: { ...layout.panels.left, size: sizes[0] },
          right: { ...layout.panels.right, size: sizes[2] },
        },
      });
    }
  };

  const persistBottomSize = (sizes: number[]) => {
    if (sizes.length === 2) {
      setLayout({
        panels: { ...layout.panels, bottom: { ...layout.panels.bottom, size: sizes[1] } },
      });
    }
  };

  const persistRightAISize = (sizes: number[]) => {
    if (sizes.length === 2) {
      setLayout({
        panels: { ...layout.panels, rightAI: { ...layout.panels.rightAI, size: sizes[0] } },
      });
    }
  };

  const toggleAIPanel = () => {
    const collapsed = !layout.panels.rightAI.collapsed;
    setLayout({ panels: { ...layout.panels, rightAI: { ...layout.panels.rightAI, collapsed } } });
    if (rightAIPanelRef.current) {
      if (collapsed) rightAIPanelRef.current.collapse();
      else rightAIPanelRef.current.expand();
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    window.location.href = '/login';
  };

  if (!authed || !ready) {
    return (
      <div className="ws-root">
        <div style={{ margin: 'auto' }} className="ws-muted">
          載入工作台…
        </div>
      </div>
    );
  }

  const watchlistPanel = (
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
    />
  );

  const chartBlock = (
    <div className="ws-panel" style={{ position: 'relative' }}>
      {barsError ? (
        <div className="ws-message-error" style={{ margin: 10 }}>
          {barsError}
        </div>
      ) : (
        <PriceChart
          data={bars}
          loading={barsLoading}
          range={layout.range}
          onRangeChange={(r: RangeKey) => setLayout({ range: r })}
        />
      )}
    </div>
  );

  const bottomTabs = (
    <BottomTabs active={layout.bottomTab} onChange={(tab) => setLayout({ bottomTab: tab })} />
  );

  const detailsAndOrder = (
    <>
      <DetailsPanel instrumentId={symbol} />
      <div style={{ height: 6, background: '#131722', flexShrink: 0 }} />
      <OrderPreviewPanel instrument={detail} bars={bars} instrumentId={symbol} />
    </>
  );

  return (
    <div className="ws-root">
      <header className="ws-topbar">
        <span className="ws-topbar-brand">📊 {brand.name}</span>
        <SymbolSearch onSelect={onSelectSymbol} />
        <StatusChips />
        <div className="ws-topbar-links">
          <button
            className="ws-icon-btn ws-mobile-only"
            style={{ fontSize: 16 }}
            title="觀察列表"
            onClick={() => setMobileDrawer((v) => !v)}
            data-testid="mobile-watchlist-toggle"
          >
            ☰
          </button>
          <Link href="/dashboard">主頁</Link>
          <Link href="/training">訓練</Link>
          <Link href="/subscription">訂閱</Link>
          <button onClick={handleLogout}>登出</button>
        </div>
      </header>

      {isMobile ? (
        <div className="ws-main" data-testid="workspace-mobile">
          {mobileDrawer && <div style={{ borderBottom: '1px solid #2a2e39' }}>{watchlistPanel}</div>}
          <div style={{ height: 320, flexShrink: 0 }}>{chartBlock}</div>
          <div style={{ height: 420, flexShrink: 0 }}>
            <AIPanel currentSymbol={symbol} />
          </div>
          {detailsAndOrder}
          {bottomTabs}
        </div>
      ) : (
        <div className="ws-main">
          <PanelGroup direction="horizontal" onLayout={persistHorizontalSizes}>
            <Panel
              defaultSize={layout.panels.left.size}
              minSize={8}
              collapsible
              ref={leftPanelRef}
              onCollapse={() => setLayout({ panels: { ...layout.panels, left: { ...layout.panels.left, collapsed: true } } })}
              onExpand={() => setLayout({ panels: { ...layout.panels, left: { ...layout.panels.left, collapsed: false } } })}
            >
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                <div className="ws-panel-header">
                  觀察列表
                  <div className="ws-panel-header-actions">
                    <button className="ws-icon-btn" title="收合" onClick={() => togglePanel('left')}>
                      ⟨
                    </button>
                  </div>
                </div>
                <div style={{ flex: 1, minHeight: 0 }}>{watchlistPanel}</div>
              </div>
            </Panel>
            <PanelResizeHandle className="ws-desktop-only" style={{ width: 4, background: '#131722', cursor: 'col-resize' }} />
            <Panel minSize={30}>
              <PanelGroup direction="vertical" onLayout={persistBottomSize}>
                <Panel minSize={20}>
                  <div style={{ height: '100%' }}>{chartBlock}</div>
                </Panel>
                <PanelResizeHandle style={{ height: 4, background: '#131722', cursor: 'row-resize' }} />
                <Panel
                  defaultSize={layout.panels.bottom.size}
                  minSize={8}
                  collapsible
                  ref={bottomPanelRef}
                  onCollapse={() => setLayout({ panels: { ...layout.panels, bottom: { ...layout.panels.bottom, collapsed: true } } })}
                  onExpand={() => setLayout({ panels: { ...layout.panels, bottom: { ...layout.panels.bottom, collapsed: false } } })}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                    <div className="ws-panel-header">
                      工作數據
                      <div className="ws-panel-header-actions">
                        <button className="ws-icon-btn" title="收合" onClick={() => togglePanel('bottom')}>
                          ⌄
                        </button>
                      </div>
                    </div>
                    <div style={{ flex: 1, minHeight: 0 }}>{bottomTabs}</div>
                  </div>
                </Panel>
              </PanelGroup>
            </Panel>
            <PanelResizeHandle style={{ width: 4, background: '#131722', cursor: 'col-resize' }} />
            <Panel
              defaultSize={layout.panels.right.size}
              minSize={10}
              collapsible
              ref={rightPanelRef}
              onCollapse={() => setLayout({ panels: { ...layout.panels, right: { ...layout.panels.right, collapsed: true } } })}
              onExpand={() => setLayout({ panels: { ...layout.panels, right: { ...layout.panels.right, collapsed: false } } })}
            >
              <PanelGroup direction="vertical" onLayout={persistRightAISize}>
                <Panel
                  defaultSize={layout.panels.rightAI.size}
                  minSize={10}
                  collapsible
                  ref={rightAIPanelRef}
                  onCollapse={() => setLayout({ panels: { ...layout.panels, rightAI: { ...layout.panels.rightAI, collapsed: true } } })}
                  onExpand={() => setLayout({ panels: { ...layout.panels, rightAI: { ...layout.panels.rightAI, collapsed: false } } })}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                    <div className="ws-panel-header">
                      AI 助手
                      <div className="ws-panel-header-actions">
                        <button className="ws-icon-btn" title="收合 AI 面板" onClick={toggleAIPanel}>
                          ⌃
                        </button>
                      </div>
                    </div>
                    <div style={{ flex: 1, minHeight: 0 }}>
                      <AIPanel currentSymbol={symbol} />
                    </div>
                  </div>
                </Panel>
                <PanelResizeHandle style={{ height: 4, background: '#131722', cursor: 'row-resize' }} />
                <Panel minSize={10}>
                  <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                    <div className="ws-panel-header">
                      詳情 / 下單
                      <div className="ws-panel-header-actions">
                        <button className="ws-icon-btn" title="收合" onClick={() => togglePanel('right')}>
                          ⟩
                        </button>
                      </div>
                    </div>
                    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>{detailsAndOrder}</div>
                  </div>
                </Panel>
              </PanelGroup>
            </Panel>
          </PanelGroup>
        </div>
      )}
    </div>
  );
}

export type { WorkspaceLayout };
