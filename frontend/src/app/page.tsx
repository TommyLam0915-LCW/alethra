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
    <div className="container" style={{ paddingTop: '60px', paddingBottom: '60px' }}>
      <div style={{ textAlign: 'center', marginBottom: '60px' }}>
        <p style={{ fontSize: '14px', letterSpacing: '2px', color: '#999', textTransform: 'uppercase', marginBottom: '16px' }}>
          {brand.name} — {brand.productDescriptor.zh}
        </p>
        <h1 style={{ fontSize: '40px', marginBottom: '24px', lineHeight: '1.3' }}>
          {brand.ui.landing.heroTitle}
        </h1>
        <p style={{ fontSize: '18px', color: '#555', maxWidth: '700px', margin: '0 auto 40px', lineHeight: '1.7' }}>
          {brand.ui.landing.heroSubtitle}
        </p>

        <div style={{ display: 'flex', gap: '16px', justifyContent: 'center' }}>
          {isLoggedIn ? (
            <Link href="/dashboard" className="btn btn-primary">
              前往控制台
            </Link>
          ) : (
            <>
              <Link href="/login" className="btn btn-primary">
                登入
              </Link>
              <Link href="/login" className="btn btn-secondary">
                註冊
              </Link>
            </>
          )}
        </div>
      </div>

      <div className="disclaimer">
        <strong>重要聲明：</strong>{brand.disclaimer.zhFull}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '24px', marginTop: '40px' }}>
        {brand.ui.landing.features.map((feature, idx) => (
          <div key={idx} className="card">
            <h3 style={{ marginBottom: '12px' }}>
              {idx === 0 && '📊'}
              {idx === 1 && '🔬'}
              {idx === 2 && '⚠️'}
              {' '}{feature.titleZh}
            </h3>
            <p style={{ color: '#666' }}>
              {feature.descriptionZh}
            </p>
          </div>
        ))}
      </div>

      <div style={{ marginTop: '60px', textAlign: 'center', color: '#999', fontSize: '14px' }}>
        <p>{brand.footer.textZh}</p>
      </div>
    </div>
  );
}
