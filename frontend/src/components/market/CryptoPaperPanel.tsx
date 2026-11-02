'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ApiError, api, formatErrorDetail } from '@/lib/api';

interface IntervalOption {
  label: string;
  minutes: number;
}

interface Props {
  instrumentId: string;
  instrumentLabel: string;
  pricePrecision?: number;
  intervalLabel: string;
  intervals: IntervalOption[];
}

interface PortfolioRow {
  id: string;
  currency: string;
  status: string;
  current_virtual_cash: number;
}

interface Refusal {
  code: string;
  message: string;
}

const PAPER_INTERVALS = [1, 5];

function refusalFrom(err: unknown): Refusal {
  if (err instanceof ApiError && err.detail && typeof err.detail === 'object') {
    const detail = err.detail as { code?: unknown; message?: unknown };
    if (typeof detail.code === 'string') {
      return {
        code: detail.code,
        message:
          typeof detail.message === 'string'
            ? detail.message
            : formatErrorDetail(err.detail),
      };
    }
    return { code: 'request_refused', message: formatErrorDetail(err.detail) };
  }
  return { code: 'request_failed', message: err instanceof Error ? err.message : String(err) };
}

function utcLabel(value: string | null | undefined): string {
  if (!value) return '—';
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/.exec(value);
  if (!match) return value;
  return `${match[1]} ${match[2]} UTC`;
}

function money(value: string | null | undefined, unit: string): string {
  if (value === null || value === undefined || value === '') return '—';
  return `${value} ${unit}`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        gap: 16,
        padding: '6px 0',
        borderBottom: '1px solid var(--border-subtle)',
        fontSize: 13,
      }}
    >
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span className="num" style={{ textAlign: 'right', wordBreak: 'break-all' }}>
        {children}
      </span>
    </div>
  );
}

const inputStyle = {
  width: '100%',
  padding: '7px 10px',
  border: '1px solid var(--border-subtle)',
  borderRadius: 4,
  backgroundColor: 'var(--surface-2)',
  color: 'var(--text-primary)',
  fontSize: 13,
} as const;

const labelStyle = {
  display: 'flex',
  flexDirection: 'column',
  gap: 6,
  fontSize: 12,
  color: 'var(--text-secondary)',
} as const;

export default function CryptoPaperPanel({
  instrumentId,
  instrumentLabel,
  pricePrecision,
  intervalLabel,
  intervals,
}: Props) {
  const [portfolios, setPortfolios] = useState<PortfolioRow[] | null>(null);
  const [portfolioError, setPortfolioError] = useState<string | null>(null);
  const [portfolioId, setPortfolioId] = useState('');
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [quantity, setQuantity] = useState('0.01');
  const [intervalMinutes, setIntervalMinutes] = useState(1);
  const [quote, setQuote] = useState<Record<string, any> | null>(null);
  const [receipt, setReceipt] = useState<Record<string, any> | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [capture, setCapture] = useState<Record<string, any> | null>(null);
  const [captureRefusal, setCaptureRefusal] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<{ cash: number; quantity: number; entry: number | null } | null>(
    null,
  );

  const pageMinutes = useMemo(
    () => intervals.find((option) => option.label === intervalLabel)?.minutes ?? null,
    [intervals, intervalLabel],
  );

  useEffect(() => {
    if (pageMinutes && PAPER_INTERVALS.includes(pageMinutes)) setIntervalMinutes(pageMinutes);
  }, [pageMinutes]);

  const loadPortfolios = useCallback(async () => {
    try {
      const rows = (await api.paperPortfolio.list()) as PortfolioRow[];
      const usable = rows.filter(
        (row) => row.currency === 'USD' && row.status === 'active',
      );
      setPortfolios(usable);
      setPortfolioError(null);
      setPortfolioId((current) => {
        if (current && usable.some((row) => row.id === current)) return current;
        return usable[0]?.id ?? '';
      });
    } catch (err) {
      setPortfolios([]);
      setPortfolioError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadPortfolios();
  }, [loadPortfolios]);

  // A quote is tied to the exact order it priced. Changing pair, side, size or
  // interval invalidates it, so the submit button can never book a stale price.
  useEffect(() => {
    setQuote(null);
    setRefusal(null);
    setReceipt(null);
    setCapture(null);
    setCaptureRefusal(null);
  }, [instrumentId, side, quantity, intervalMinutes, portfolioId]);

  const loadSummary = useCallback(async (id: string, assetId: string) => {
    try {
      const [portfolio, positions] = await Promise.all([
        api.paperPortfolio.get(id) as Promise<Record<string, any>>,
        api.paperPortfolio.positions(id) as Promise<Array<Record<string, any>>>,
      ]);
      const position = positions.find(
        (row) => row.asset_id === assetId && row.status === 'open',
      );
      setSummary({
        cash: Number(portfolio.current_virtual_cash),
        quantity: position ? Number(position.quantity) : 0,
        entry: position ? Number(position.average_entry_price) : null,
      });
    } catch {
      setSummary(null);
    }
  }, []);

  const preview = useCallback(async () => {
    if (!portfolioId) return;
    setBusy(true);
    setRefusal(null);
    setReceipt(null);
    try {
      const data = (await api.cryptoPaper.quote({
        instrument_id: instrumentId,
        side,
        quantity,
        interval_minutes: intervalMinutes,
      })) as Record<string, any>;
      setQuote(data);
    } catch (err) {
      setQuote(null);
      setRefusal(refusalFrom(err));
    } finally {
      setBusy(false);
    }
  }, [portfolioId, instrumentId, side, quantity, intervalMinutes]);

  // One Kraken REST read under the shared upstream budget. It writes completed
  // bars only; the forming candle stays excluded, and no order is booked.
  const captureBars = useCallback(async () => {
    setBusy(true);
    setCapture(null);
    setCaptureRefusal(null);
    setRefusal(null);
    try {
      const data = (await api.cryptoPaper.captureQuote({
        instrument_id: instrumentId,
        interval_minutes: intervalMinutes,
      })) as Record<string, any>;
      setCapture(data);
      setQuote(null);
    } catch (err) {
      setCaptureRefusal(refusalFrom(err));
    } finally {
      setBusy(false);
    }
  }, [instrumentId, intervalMinutes]);

  const submit = useCallback(async () => {
    if (!portfolioId || !quote) return;
    setBusy(true);
    setRefusal(null);
    try {
      const data = (await api.cryptoPaper.submitOrder(portfolioId, {
        instrument_id: instrumentId,
        interval_minutes: intervalMinutes,
        side,
        quantity,
        idempotency_key: crypto.randomUUID(),
        rationale: `Crypto paper order from the ${instrumentLabel} research page`,
        expected_outcome: 'Accounting matches the receipt captured with the fill',
      })) as Record<string, any>;
      setReceipt(data);
      setQuote(null);
      await loadSummary(portfolioId, instrumentId);
    } catch (err) {
      setRefusal(refusalFrom(err));
    } finally {
      setBusy(false);
    }
  }, [portfolioId, quote, instrumentId, intervalMinutes, side, quantity, instrumentLabel, loadSummary]);

  const downloadCsv = useCallback(async () => {
    if (!portfolioId) return;
    try {
      const text = await api.cryptoPaper.executionsCsv(portfolioId);
      const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `crypto-paper-executions-${portfolioId}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setRefusal(refusalFrom(err));
    }
  }, [portfolioId]);

  const unit = portfolioId
    ? portfolios?.find((row) => row.id === portfolioId)?.currency ?? 'USD'
    : 'USD';
  const quoteIsFixture = quote?.data_nature === 'fixture';
  const receiptIsFixture = receipt?.data_nature === 'fixture';
  const intervalSupported = PAPER_INTERVALS.includes(intervalMinutes);
  const pageIntervalUnsupported = pageMinutes !== null && !PAPER_INTERVALS.includes(pageMinutes);

  return (
    <section
      aria-labelledby="crypto-paper-heading"
      data-testid="crypto-paper-panel"
      style={{
        border: '1px solid var(--border-subtle)',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 6,
        padding: 20,
        marginBottom: 20,
      }}
    >
      <h2 id="crypto-paper-heading" style={{ margin: '0 0 8px', fontSize: 15, fontWeight: 600 }}>
        Simulated paper orders
      </h2>
      <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--text-secondary)' }}>
        An order here fills against the latest completed candle on this page, using the same paper
        ledger as the rest of the product. Slippage and commission are simulated assumptions, not
        your Kraken fee schedule. No order is sent to Kraken and no trading key is read. A market
        update on this page is a read; only the button below books a simulated fill. If the quote
        window has no completed bar, fetch the latest bars from the venue first — one public read
        that stores closed candles only.
      </p>

      {portfolioError && (
        <div
          role="alert"
          style={{
            border: '1px solid var(--negative)',
            borderRadius: 6,
            padding: 12,
            marginBottom: 16,
            fontSize: 13,
          }}
        >
          Couldn&apos;t read your paper portfolios: {portfolioError}
        </div>
      )}

      {portfolios !== null && portfolios.length === 0 && !portfolioError && (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }} data-testid="crypto-paper-no-portfolio">
          You have no active USD paper portfolio. <Link href="/paper-portfolio">Create one</Link> to
          place simulated orders. Quotes can still be previewed once a portfolio exists.
        </div>
      )}

      {portfolios !== null && portfolios.length > 0 && (
        <>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 12,
              marginBottom: 16,
            }}
          >
            <label style={labelStyle}>
              Paper portfolio
              <select
                aria-label="Paper portfolio"
                data-testid="crypto-paper-portfolio"
                value={portfolioId}
                onChange={(event) => setPortfolioId(event.target.value)}
                style={inputStyle}
              >
                {portfolios.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.id.slice(0, 8)} · {row.currency}
                  </option>
                ))}
              </select>
            </label>
            <label style={labelStyle}>
              Side
              <select
                aria-label="Order side"
                data-testid="crypto-paper-side"
                value={side}
                onChange={(event) => setSide(event.target.value as 'BUY' | 'SELL')}
                style={inputStyle}
              >
                <option value="BUY">Buy</option>
                <option value="SELL">Sell</option>
              </select>
            </label>
            <label style={labelStyle}>
              Quantity ({instrumentLabel.split('/')[0] ?? 'asset'})
              <input
                aria-label="Order quantity"
                data-testid="crypto-paper-quantity"
                value={quantity}
                inputMode="decimal"
                onChange={(event) => setQuantity(event.target.value)}
                style={inputStyle}
              />
            </label>
            <label style={labelStyle}>
              Candle interval
              <select
                aria-label="Paper interval"
                data-testid="crypto-paper-interval"
                value={intervalMinutes}
                onChange={(event) => setIntervalMinutes(Number(event.target.value))}
                style={inputStyle}
              >
                {PAPER_INTERVALS.map((minutes) => (
                  <option key={minutes} value={minutes}>
                    {minutes}m
                  </option>
                ))}
              </select>
            </label>
          </div>

          {pageIntervalUnsupported && (
            <div style={{ fontSize: 12, color: 'var(--warning-text)', marginBottom: 12 }}>
              The chart is showing {intervalLabel}, which the paper lane does not price. Paper orders
              use a completed 1m or 5m bar.
            </div>
          )}

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => void captureBars()}
              disabled={busy || !intervalSupported}
              data-testid="crypto-paper-capture"
              title="One public Kraken REST read of the latest completed bars"
            >
              {busy && !capture && !quote ? 'Fetching venue bars…' : 'Fetch latest venue bars'}
            </button>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => void preview()}
              disabled={busy || !portfolioId || !intervalSupported}
              data-testid="crypto-paper-preview"
            >
              {busy && !quote ? 'Reading quote…' : 'Preview quote'}
            </button>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => void submit()}
              disabled={busy || !quote}
              data-testid="crypto-paper-submit"
            >
              {busy && quote ? 'Booking…' : 'Place simulated order'}
            </button>
          </div>

          {captureRefusal && (
            <div
              role="alert"
              data-testid="crypto-paper-capture-refusal"
              style={{
                border: '1px solid var(--negative)',
                backgroundColor: 'rgba(226,99,110,0.1)',
                borderRadius: 6,
                padding: 12,
                marginBottom: 16,
                fontSize: 13,
              }}
            >
              <strong>Venue read refused ({captureRefusal.code}):</strong> {captureRefusal.message}
            </div>
          )}

          {capture && capture.status === 'captured' && (
            <div
              data-testid="crypto-paper-capture-result"
              style={{
                border: '1px solid var(--border-subtle)',
                borderRadius: 4,
                padding: 16,
                marginBottom: 16,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  gap: 8,
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  marginBottom: 8,
                }}
              >
                <strong style={{ fontSize: 13 }}>Venue bars captured</strong>
                <span
                  style={{
                    fontSize: 12,
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 999,
                    padding: '2px 10px',
                    color: 'var(--warning-text)',
                  }}
                >
                  {capture.data_nature} · read-only
                </span>
              </div>
              <Row label="Bars stored / refreshed">
                {capture.stored} stored · {capture.updated} refreshed
              </Row>
              <Row label="Price source">{capture.price_source}</Row>
              <Row label="Latest completed bar">
                {capture.latest_completed_bar
                  ? `${utcLabel(capture.latest_completed_bar.open_time)} · close ${capture.latest_completed_bar.close}`
                  : '—'}
              </Row>
              <Row label="Forming candle">
                {capture.forming_excluded ? 'Excluded — still open' : 'None returned'}
              </Row>
              <Row label="Quote window">{capture.quote_window_seconds}s</Row>
              <Row label="Fingerprint">{capture.fingerprint}</Row>
              <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
                Preview a quote to price an order on these bars. Nothing was booked.
              </div>
            </div>
          )}

          {capture && capture.status === 'no_data' && (
            <div
              role="alert"
              data-testid="crypto-paper-capture-result"
              style={{
                border: '1px solid var(--warning-text)',
                borderRadius: 6,
                padding: 12,
                marginBottom: 16,
                fontSize: 13,
              }}
            >
              {capture.message}
            </div>
          )}

          {refusal && (
            <div
              role="alert"
              data-testid="crypto-paper-refusal"
              style={{
                border: '1px solid var(--negative)',
                backgroundColor: 'rgba(226,99,110,0.1)',
                borderRadius: 6,
                padding: 12,
                marginBottom: 16,
                fontSize: 13,
              }}
            >
              <strong>Refused ({refusal.code}):</strong> {refusal.message}
            </div>
          )}

          {quote && (
            <div
              data-testid="crypto-paper-quote"
              style={{
                border: '1px solid var(--border-subtle)',
                borderRadius: 4,
                padding: 16,
                marginBottom: 16,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  gap: 8,
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  marginBottom: 8,
                }}
              >
                <strong style={{ fontSize: 13 }}>Order summary</strong>
                <span
                  style={{
                    fontSize: 12,
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 999,
                    padding: '2px 10px',
                    color: 'var(--warning-text)',
                  }}
                >
                  Simulated · no venue order
                </span>
                {quote.label && (
                  <span
                    data-testid={
                      quoteIsFixture ? 'crypto-paper-fixture-label' : 'crypto-paper-source-label'
                    }
                    style={{
                      fontSize: 12,
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 999,
                      padding: '2px 10px',
                      color: 'var(--warning-text)',
                    }}
                  >
                    {quote.label}
                  </span>
                )}
              </div>
              <Row label="Pair">
                {quote.instrument_id} · {quote.venue}
              </Row>
              <Row label="Side / quantity">
                {quote.side} {quote.quantity} {quote.base_asset}
              </Row>
              <Row label={`Observed price (${quote.quote_asset})`}>
                {quote.observed_price}
                {pricePrecision !== undefined ? ` · ${pricePrecision} dp` : ''}
              </Row>
              <Row label={`Execution price (${quote.quote_asset})`}>{quote.execution_price}</Row>
              <Row label="Gross">{money(quote.gross_amount, quote.quote_asset)}</Row>
              <Row label={`Commission (${quote.commission_rate})`}>
                {money(quote.commission_amount, quote.quote_asset)}
              </Row>
              <Row label={`Slippage (${quote.slippage_rate})`}>
                {money(quote.slippage_amount, quote.quote_asset)}
              </Row>
              <Row label="Net cash change">{money(quote.net_cash_change, quote.quote_asset)}</Row>
              <Row label="Bar used">
                {utcLabel(quote.bar_open_time)} · {quote.interval_minutes}m · {quote.data_nature}
              </Row>
              <Row label="Price source">{quote.price_source}</Row>
              <Row label="Snapshot fingerprint">{quote.snapshot_fingerprint}</Row>
              <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
                {quote.cost_model}
              </div>
            </div>
          )}

          {receipt && (
            <div
              data-testid="crypto-paper-receipt"
              style={{
                border: '1px solid var(--positive)',
                borderRadius: 4,
                padding: 16,
                marginBottom: 16,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  gap: 8,
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  marginBottom: 8,
                }}
              >
                <strong style={{ fontSize: 13 }}>
                  {receipt.created ? 'Simulated fill booked' : 'Existing fill returned (same idempotency key)'}
                </strong>
                {receipt.label && (
                  <span
                    data-testid={
                      receiptIsFixture
                        ? 'crypto-paper-receipt-fixture-label'
                        : 'crypto-paper-receipt-source-label'
                    }
                    style={{
                      fontSize: 12,
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 999,
                      padding: '2px 10px',
                      color: 'var(--warning-text)',
                    }}
                  >
                    {receipt.label}
                  </span>
                )}
              </div>
              <Row label="Order / fill / receipt">
                {receipt.order_id} · {receipt.fill_id} · {receipt.receipt_id}
              </Row>
              <Row label="Net cash change (receipt)">
                {money(receipt.net_cash_change, receipt.quote_asset)}
              </Row>
              <Row label="Booked on the shared ledger">
                {money(receipt.ledger?.net_cash_change, receipt.quote_asset)}
              </Row>
              <Row label="Ledger mirror">{receipt.ledger?.mirror}</Row>
              <Row label="Bar used">
                {utcLabel(receipt.bar_open_time)} · {receipt.interval_minutes}m ·{' '}
                {receipt.data_nature}
              </Row>
              <Row label="Snapshot fingerprint">{receipt.snapshot_fingerprint}</Row>
              {summary && (
                <>
                  <Row label="Portfolio cash after fill">
                    {money(summary.cash.toFixed(4), unit)}
                  </Row>
                  <Row label={`${receipt.base_asset} position`}>
                    {summary.quantity} {summary.quantity > 0 ? `· avg entry ${summary.entry}` : ''}
                  </Row>
                </>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13 }}>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => void downloadCsv()}
              data-testid="crypto-paper-csv"
            >
              Download receipts (CSV)
            </button>
            <Link
              href={`/paper-portfolio/${portfolioId}`}
              style={{ color: 'var(--text-secondary)', alignSelf: 'center' }}
            >
              Open portfolio journal
            </Link>
          </div>
        </>
      )}
    </section>
  );
}
