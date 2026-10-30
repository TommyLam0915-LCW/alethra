'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { statusPillClass } from '@/lib/status-styles';
import { formatDate } from '@/lib/format';

interface Plan {
  id: string;
  name: string;
  description: string | null;
  monthly_price: number;
  currency: string;
  features: Record<string, any> | null;
}

interface Subscription {
  id: string;
  plan_id: string;
  status: string;
  current_period_start: string | null;
  current_period_end: string | null;
}

interface Entitlement {
  id: string;
  pilot_access: boolean;
  research_run_limit: number | null;
  ai_usage_limit: number | null;
  export_limit: number | null;
  forward_simulation_limit: number | null;
  expiry_date: string | null;
  support_tier: string;
}

interface OperationalStatus {
  feature_switches: Record<string, boolean>;
  mode: string;
  data_mode: string;
  broker_mode: string;
  ai_mode: string;
}

export default function SubscriptionPage() {
  const router = useRouter();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null);
  const [opStatus, setOpStatus] = useState<OperationalStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [subscriptionError, setSubscriptionError] = useState('');
  const [entitlementError, setEntitlementError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadData();
  }, [router]);

  const loadData = async () => {
    try {
      setLoading(true);
      setError('');
      setSubscriptionError('');
      setEntitlementError('');

      const [plansRes, opStatusRes] = await Promise.all([
        api.billing.listPlans(),
        api.billing.getOperationalStatus().catch(() => null),
      ]);
      setPlans(plansRes.plans || []);
      setOpStatus(opStatusRes);

      try {
        const subRes = await api.billing.getMySubscription();
        setSubscription(subRes.subscription || null);
      } catch (err: any) {
        setSubscription(null);
        setSubscriptionError(err.message || '無法載入訂閱資訊');
      }

      try {
        const ent = await api.pilot.getMyEntitlement();
        setEntitlement(ent);
      } catch (err: any) {
        if (err instanceof ApiError && err.status === 404) {
          setEntitlement(null);
        } else {
          setEntitlement(null);
          setEntitlementError(err.message || '無法載入配額資訊');
        }
      }
    } catch (err: any) {
      console.error('Failed to load subscription data:', err);
      setError(err.message || '載入失敗');
    } finally {
      setLoading(false);
    }
  };

  const getStatusLabel = (status: string) => {
    const labels: Record<string, string> = {
      trialing: '試用中',
      active: '啟用中',
      past_due: '逾期',
      cancelled: '已取消',
      expired: '已過期',
    };
    return labels[status] || status;
  };

  const formatPrice = (price: number, currency: string) => {
    const amount = (price / 100).toFixed(2);
    const symbols: Record<string, string> = { USD: 'US$', TWD: 'NT$' };
    return `${symbols[currency] || currency} ${amount}`;
  };

  const getModeLabelZh = (mode: string) => {
    const labels: Record<string, string> = {
      test: '測試模式',
      live: '正式模式',
      demo: '演示資料',
      sandbox: '沙盒',
      'paper-only': '僅模擬',
      mock: '模擬',
    };
    return labels[mode] || mode;
  };

  const renderFeatureList = (features: Record<string, any> | null) => {
    if (!features) return null;
    const items = Object.entries(features).map(([key, value]) => {
      const labels: Record<string, string> = {
        max_experiments: '最大實驗數',
        max_runs_per_month: '每月研究執行次數',
        max_exports: '最大匯出次數',
        ai_assistance: 'AI 輔助',
        forward_simulation: '正向模擬',
        priority_support: '優先支援',
      };
      const label = labels[key] || key;
      const displayValue = typeof value === 'boolean'
        ? (value ? '支援' : '不支援')
        : String(value);
      return (
        <li key={key} style={{ color: 'var(--text-secondary)', fontSize: '14px', marginBottom: '4px' }}>
          {label}：{displayValue}
        </li>
      );
    });
    return <ul style={{ listStyle: 'none', padding: 0, margin: '12px 0 0 0' }}>{items}</ul>;
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1>訂閱方案</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/dashboard" className="btn btn-secondary">
            返回我的實驗
          </Link>
        </div>
      </div>

      {/* Test billing banner */}
      <div className="notice notice-warn" role="note" style={{ fontSize: '14px', padding: '16px 20px' }}>
        <strong>測試計費系統</strong> — 不會處理真實付款。所有方案與訂閱均為測試用途，不產生實際費用。
      </div>

      {error && (
        <div className="notice notice-danger" role="alert" style={{ fontSize: '14px', padding: '16px 20px' }}>
          錯誤：{error}
        </div>
      )}

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中...
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '24px' }}>
          {/* Operational Status */}
          {opStatus && (
            <div className="card" style={{ padding: '20px' }}>
              <h3 style={{ marginBottom: '12px' }}>系統狀態</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' }}>
                <div>
                  <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>付款模式</span>
                  <span className={`status-pill status-${opStatus.mode === 'test' ? 'warn' : 'ok'}`} style={{ marginLeft: '8px' }}>
                    {getModeLabelZh(opStatus.mode)}
                  </span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>資料模式</span>
                  <span className={`status-pill status-${opStatus.data_mode === 'demo' ? 'info' : 'ok'}`} style={{ marginLeft: '8px' }}>
                    {getModeLabelZh(opStatus.data_mode)}
                  </span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>交易模式</span>
                  <span style={{ marginLeft: '8px', fontSize: '14px' }}>{getModeLabelZh(opStatus.broker_mode)}</span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>AI 模式</span>
                  <span style={{ marginLeft: '8px', fontSize: '14px' }}>{getModeLabelZh(opStatus.ai_mode)}</span>
                </div>
              </div>
            </div>
          )}

          {/* Current Subscription */}
          <div className="card" style={{ padding: '20px' }}>
            <h3 style={{ marginBottom: '16px' }}>目前方案</h3>
            {subscriptionError && (
              <div className="notice notice-danger" role="alert" style={{ fontSize: '14px' }}>
                {subscriptionError}
              </div>
            )}
            {subscription ? (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                  <span style={{ fontSize: '18px', fontWeight: 600 }}>
                    {plans.find((p) => p.id === subscription.plan_id)?.name || '付費方案'}
                  </span>
                  <span className={statusPillClass(subscription.status)}>
                    {getStatusLabel(subscription.status)}
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px', fontSize: '14px' }}>
                  {subscription.current_period_start && (
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>計費週期開始：</span>
                      <span>{formatDate(subscription.current_period_start)}</span>
                    </div>
                  )}
                  {subscription.current_period_end && (
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>計費週期結束：</span>
                      <span>{formatDate(subscription.current_period_end)}</span>
                    </div>
                  )}
                </div>
              </div>
            ) : !subscriptionError ? (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                  <span style={{ fontSize: '18px', fontWeight: 600 }}>免費方案</span>
                  <span style={{
                    padding: '4px 12px',
                    borderRadius: '12px',
                    fontSize: '12px',
                    backgroundColor: 'rgba(255,255,255,0.10)',
                    color: 'var(--text-secondary)',
                  }}>
                    基礎
                  </span>
                </div>
                {entitlement && (
                  <div style={{ fontSize: '14px' }}>
                    <p style={{ color: 'var(--text-secondary)', marginBottom: '8px' }}>你目前的配額上限：</p>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '8px' }}>
                      <div>研究執行次數上限：{entitlement.research_run_limit == null ? '無上限' : entitlement.research_run_limit <= 0 ? '無配額' : entitlement.research_run_limit}</div>
                      <div>AI 使用次數上限：{entitlement.ai_usage_limit == null ? '無上限' : entitlement.ai_usage_limit <= 0 ? '無配額' : entitlement.ai_usage_limit}</div>
                      <div>匯出次數上限：{entitlement.export_limit == null ? '無上限' : entitlement.export_limit <= 0 ? '無配額' : entitlement.export_limit}</div>
                      <div>正向模擬上限：{entitlement.forward_simulation_limit == null ? '無上限' : entitlement.forward_simulation_limit <= 0 ? '無配額' : entitlement.forward_simulation_limit}</div>
                    </div>
                    {entitlement.expiry_date && (
                      <p style={{ color: 'var(--text-muted)', marginTop: '8px', fontSize: '13px' }}>
                        試用期限：{formatDate(entitlement.expiry_date)}
                      </p>
                    )}
                  </div>
                )}
                {!entitlement && (
                  <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>
                    基礎免費方案 — 可建立實驗並執行研究。你可以先從一個實驗開始，記錄假設並執行回測。
                  </p>
                )}
              </div>
            ) : null}
          </div>

          {/* Entitlements / Quota Usage */}
          {entitlement && (
            <div className="card" style={{ padding: '20px' }}>
              <h3 style={{ marginBottom: '16px' }}>功能配額</h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: '14px', marginBottom: '16px' }}>
                以下為你的帳戶目前可使用之各項功能上限。
              </p>
              <div style={{ display: 'grid', gap: '12px' }}>
                {[
                  { label: '研究執行次數', limit: entitlement.research_run_limit },
                  { label: 'AI 使用次數', limit: entitlement.ai_usage_limit },
                  { label: '匯出次數', limit: entitlement.export_limit },
                  { label: '正向模擬次數', limit: entitlement.forward_simulation_limit },
                ].map((item) => (
                  <div key={item.label} style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '10px 14px',
                    backgroundColor: 'var(--surface-2)',
                    borderRadius: '6px',
                    fontSize: '14px',
                  }}>
                    <span>{item.label}</span>
                    <span style={{ fontWeight: 600, color: item.limit != null && item.limit <= 0 ? 'var(--negative)' : 'var(--text-primary)' }}>
                      {item.limit == null ? '無上限' : item.limit <= 0 ? '無配額（無法使用）' : item.limit}
                    </span>
                  </div>
                ))}
              </div>
              <p style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '12px' }}>
                支援層級：{entitlement.support_tier || '基礎'}
              </p>
            </div>
          )}
          {entitlementError && !entitlement && (
            <div className="card" style={{ padding: '20px' }}>
              <h3 style={{ marginBottom: '12px' }}>功能配額</h3>
              <div className="notice notice-danger" role="alert" style={{ fontSize: '14px' }}>
                {entitlementError}
              </div>
            </div>
          )}

          {/* Available Plans */}
          <div>
            <h2 style={{ marginBottom: '16px' }}>可用方案</h2>
            {plans.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
                <p style={{ color: 'var(--text-secondary)' }}>目前暫無可用方案。</p>
              </div>
            ) : (
              <div style={{ display: 'grid', gap: '16px', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
                {plans.map((plan) => {
                  const isCurrentPlan = subscription?.plan_id === plan.id;
                  return (
                    <div
                      key={plan.id}
                      className="card"
                      style={{
                        padding: '24px',
                        border: isCurrentPlan ? '2px solid var(--primary)' : '1px solid var(--border-strong)',
                        position: 'relative',
                      }}
                    >
                      {isCurrentPlan && (
                        <span style={{
                          position: 'absolute',
                          top: '12px',
                          right: '12px',
                          padding: '2px 8px',
                          borderRadius: '10px',
                          fontSize: '11px',
                          backgroundColor: 'var(--primary-solid)',
                          color: 'var(--text-white)',
                        }}>
                          目前方案
                        </span>
                      )}
                      <h3 style={{ marginBottom: '4px' }}>{plan.name}</h3>
                      {plan.description && (
                        <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '12px' }}>
                          {plan.description}
                        </p>
                      )}
                      <div style={{ fontSize: '24px', fontWeight: 700, marginBottom: '4px' }}>
                        {plan.monthly_price === 0 ? '免費' : formatPrice(plan.monthly_price, plan.currency)}
                      </div>
                      {plan.monthly_price > 0 && (
                        <span style={{ color: 'var(--text-muted)', fontSize: '13px' }}>/月</span>
                      )}
                      {renderFeatureList(plan.features)}
                      {!isCurrentPlan && plan.monthly_price > 0 && (
                        <button
                          className="btn btn-primary"
                          style={{ marginTop: '16px', width: '100%', opacity: 0.6, cursor: 'not-allowed' }}
                          disabled
                          title="測試模式 — 無法訂閱"
                        >
                          測試模式 — 無法訂閱
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Test mode disclaimer */}
          <div style={{
            textAlign: 'center',
            padding: '20px',
            color: 'var(--text-muted)',
            fontSize: '13px',
            borderTop: '1px solid var(--border-strong)',
          }}>
            此為測試計費系統 — 所有方案與交易均為模擬，不會處理任何真實付款。
          </div>
        </div>
      )}
    </div>
  );
}
