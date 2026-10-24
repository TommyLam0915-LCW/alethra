'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface DailyEquity {
  date: string;
  cash_balance: number;
  position_qty: number;
  valuation_price: number;
  equity: number;
}

interface Transaction {
  date: string;
  execution_date: string;
  action: string;
  quantity: number;
  price: number;
  commission: number;
  net_cash_change: number;
  shares_after: number;
}

interface Run {
  id: string;
  status: string;
  metrics: any;
  warnings: string[] | null;
  config_hash: string;
  dataset_checksum: string | null;
  methodology_json: any;
  daily_equity_curve: DailyEquity[] | null;
  transactions: Transaction[] | null;
  started_at: string | null;
  completed_at: string | null;
}

export default function BacktestDetailPage() {
  const router = useRouter();
  const params = useParams();
  const experimentId = params.id as string;
  const runId = params.runId as string;

  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<'daily' | 'transactions'>('daily');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadRun();
  }, [experimentId, runId, router]);

  const loadRun = async () => {
    try {
      const runsData = await api.runs.list(experimentId);
      const foundRun = runsData.find((r: Run) => r.id === runId);
      if (!foundRun) {
        setError('找不到此回測執行');
        return;
      }
      setRun(foundRun);
    } catch (err) {
      setError('載入失敗');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px' }}>載入中...</div>;
  }

  if (error || !run) {
    return (
      <div className="container" style={{ paddingTop: '40px' }}>
        <Link href={`/experiments/${experimentId}`} style={{ color: '#0070f3', textDecoration: 'none' }}>
          ← 返回實驗
        </Link>
        <div className="alert alert-warning" style={{ marginTop: '20px' }}>
          {error || '回測執行不存在'}
        </div>
      </div>
    );
  }

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href={`/experiments/${experimentId}`} style={{ color: '#0070f3', textDecoration: 'none' }}>
          ← 返回實驗
        </Link>
      </div>

      <div style={{ marginBottom: '30px' }}>
        <h1 style={{ marginBottom: '8px' }}>回測詳細結果</h1>
        <p style={{ color: '#666' }}>
          執行於 {run.started_at ? new Date(run.started_at).toLocaleString('zh-Hant') : '未知'}
          {run.completed_at && <> · 完成於 {new Date(run.completed_at).toLocaleString('zh-Hant')}</>}
        </p>
      </div>

      {run.warnings && run.warnings.length > 0 && (
        <div className="alert alert-warning" style={{ marginBottom: '20px' }}>
          <strong>警告：</strong>
          <ul style={{ marginTop: '8px', marginLeft: '20px' }}>
            {run.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="card" style={{ marginBottom: '20px' }}>
        <h3 style={{ marginBottom: '16px' }}>摘要指標</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
          {run.metrics?.total_return_pct != null && (
            <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>總報酬</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{run.metrics.total_return_pct.toFixed(2)}%</div>
            </div>
          )}
          {run.metrics?.final_equity != null && (
            <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>最終淨值</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold' }}>${run.metrics.final_equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
            </div>
          )}
          {run.metrics?.max_drawdown_pct != null && (
            <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>最大回撤</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{run.metrics.max_drawdown_pct.toFixed(2)}%</div>
            </div>
          )}
          {run.metrics?.total_trades != null && (
            <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>交易次數</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{run.metrics.total_trades}</div>
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          <button
            onClick={() => setActiveTab('daily')}
            className="btn"
            style={{
              backgroundColor: activeTab === 'daily' ? '#0070f3' : '#eaeaea',
              color: activeTab === 'daily' ? '#fff' : '#333',
            }}
          >
            每日帳務 ({run.daily_equity_curve?.length || 0})
          </button>
          <button
            onClick={() => setActiveTab('transactions')}
            className="btn"
            style={{
              backgroundColor: activeTab === 'transactions' ? '#0070f3' : '#eaeaea',
              color: activeTab === 'transactions' ? '#fff' : '#333',
            }}
          >
            交易明細 ({run.transactions?.length || 0})
          </button>
        </div>

        {activeTab === 'daily' && (
          <div>
            {!run.daily_equity_curve || run.daily_equity_curve.length === 0 ? (
              <p style={{ color: '#999', textAlign: 'center', padding: '40px' }}>
                無每日帳務資料
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid #ddd', backgroundColor: '#f5f5f5' }}>
                      <th style={{ textAlign: 'left', padding: '8px', fontWeight: '600' }}>日期</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>現金餘額</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>持倉數量</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>估值價格</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>總淨值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.daily_equity_curve.map((day, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #eee' }}>
                        <td style={{ padding: '8px' }}>{day.date}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>
                          ${day.cash_balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>{day.position_qty}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>
                          ${day.valuation_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: '8px', textAlign: 'right', fontWeight: '600' }}>
                          ${day.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {activeTab === 'transactions' && (
          <div>
            {!run.transactions || run.transactions.length === 0 ? (
              <p style={{ color: '#999', textAlign: 'center', padding: '40px' }}>
                無交易紀錄
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid #ddd', backgroundColor: '#f5f5f5' }}>
                      <th style={{ textAlign: 'left', padding: '8px', fontWeight: '600' }}>執行日期</th>
                      <th style={{ textAlign: 'left', padding: '8px', fontWeight: '600' }}>動作</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>數量</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>價格</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>手續費</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>現金變動</th>
                      <th style={{ textAlign: 'right', padding: '8px', fontWeight: '600' }}>持倉後</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.transactions.map((txn, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #eee' }}>
                        <td style={{ padding: '8px' }}>{txn.execution_date}</td>
                        <td style={{ padding: '8px' }}>
                          <span style={{
                            padding: '2px 8px',
                            borderRadius: '4px',
                            fontSize: '12px',
                            fontWeight: '500',
                            backgroundColor: txn.action === 'BUY' ? '#d4edda' : '#f8d7da',
                            color: txn.action === 'BUY' ? '#155724' : '#721c24',
                          }}>
                            {txn.action === 'BUY' ? '買入' : '賣出'}
                          </span>
                        </td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>{txn.quantity}</td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>
                          ${txn.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>
                          ${txn.commission.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: '8px', textAlign: 'right', color: txn.net_cash_change >= 0 ? '#28a745' : '#dc3545' }}>
                          {txn.net_cash_change >= 0 ? '+' : ''}${txn.net_cash_change.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: '8px', textAlign: 'right' }}>{txn.shares_after}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {run.methodology_json && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <h3 style={{ marginBottom: '12px' }}>方法論</h3>
          <div style={{ fontSize: '13px', color: '#555', lineHeight: '1.6' }}>
            <div style={{ marginBottom: '8px' }}>
              <strong>引擎版本：</strong>v{run.methodology_json.engine_version || '未知'}
            </div>
            <div style={{ marginBottom: '8px' }}>
              <strong>策略模板：</strong>{run.methodology_json.template_type || '未知'}
            </div>
            {run.methodology_json.execution_convention && (
              <div style={{ marginTop: '12px', padding: '12px', backgroundColor: '#f0f4f8', borderRadius: '6px' }}>
                <div style={{ fontWeight: '600', marginBottom: '8px' }}>執行慣例：</div>
                <div style={{ fontSize: '12px' }}>
                  {Object.entries(run.methodology_json.execution_convention).map(([key, value]) => (
                    <div key={key} style={{ marginBottom: '4px' }}>
                      <strong>{key}：</strong>{value as string}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#fff3cd', borderRadius: '6px', fontSize: '14px' }}>
        <strong>提醒：</strong>以上結果基於歷史示範資料，不代表未來績效。本系統是研究工具，不構成投資建議。
      </div>
    </div>
  );
}
