'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { brand } from '@/lib/brand';

export default function HomePage() {
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    setIsLoggedIn(!!token);
  }, []);

  return (
    <div className="dash-root">
      <div className="container" style={{ paddingTop: '80px', paddingBottom: '80px' }}>
        {/* Hero Section */}
        <div style={{ textAlign: 'center', marginBottom: '80px' }}>
          <p style={{
            fontSize: '14px',
            letterSpacing: '3px',
            color: 'var(--accent-blue)',
            textTransform: 'uppercase',
            marginBottom: '20px',
            fontWeight: '600'
          }}>
            {brand.name} — {brand.productDescriptor.zh}
          </p>
          <h1 style={{
            fontSize: '48px',
            marginBottom: '28px',
            lineHeight: '1.2',
            color: 'var(--text-white)',
            fontWeight: '700'
          }}>
            {brand.ui.landing.heroTitle}
          </h1>
          <p style={{
            fontSize: '20px',
            color: 'var(--text-secondary)',
            maxWidth: '700px',
            margin: '0 auto 48px',
            lineHeight: '1.7'
          }}>
            {brand.ui.landing.heroSubtitle}
          </p>

          <div style={{ display: 'flex', gap: '16px', justifyContent: 'center' }}>
            {isLoggedIn ? (
              <Link href="/dashboard" className="btn btn-primary" style={{ padding: '14px 32px', fontSize: '16px' }}>
                前往控制台
              </Link>
            ) : (
              <>
                <Link href="/login" className="btn btn-primary" style={{ padding: '14px 32px', fontSize: '16px' }}>
                  登入
                </Link>
                <Link href="/login" className="btn btn-secondary" style={{ padding: '14px 32px', fontSize: '16px' }}>
                  註冊
                </Link>
              </>
            )}
          </div>
        </div>

        {/* Disclaimer */}
        <div className="dash-disclaimer" style={{ maxWidth: '800px', margin: '0 auto 60px' }}>
          <strong>重要聲明：</strong>{brand.disclaimer.zhFull}
        </div>

        {/* Features Grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: '24px',
          marginTop: '60px'
        }}>
          {brand.ui.landing.features.map((feature, idx) => (
            <div key={idx} className="dash-panel" style={{ transition: 'all 0.2s' }}>
              <div className="dash-panel-body" style={{ padding: '32px' }}>
                <div style={{
                  fontSize: '48px',
                  marginBottom: '20px',
                  filter: 'grayscale(0.2)'
                }}>
                  {idx === 0 && '📊'}
                  {idx === 1 && '🔬'}
                  {idx === 2 && '⚠️'}
                </div>
                <h3 style={{
                  marginBottom: '16px',
                  fontSize: '22px',
                  color: 'var(--text-white)',
                  fontWeight: '600'
                }}>
                  {feature.titleZh}
                </h3>
                <p style={{
                  color: 'var(--text-secondary)',
                  lineHeight: '1.7',
                  fontSize: '15px'
                }}>
                  {feature.descriptionZh}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={{
          marginTop: '80px',
          textAlign: 'center',
          color: 'var(--text-muted)',
          fontSize: '14px',
          paddingTop: '40px',
          borderTop: '1px solid var(--border)'
        }}>
          <p>{brand.footer.textZh}</p>
        </div>
      </div>
    </div>
  );
}
