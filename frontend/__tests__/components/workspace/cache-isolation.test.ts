import { readCachedLayout, writeCachedLayout, clearCachedLayout, DEFAULT_LAYOUT } from '@/components/workspace/types';

// Mock JWT tokens for testing
const USER_A_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLWEtaWQiLCJuYW1lIjoiVXNlciBBIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQsW5c';
const USER_B_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLWItaWQiLCJuYW1lIjoiVXNlciBCIiwiaWF0IjoxNTE2MjM5MDIyfQ.4Adcj3UFYzPUVaVF4zFmMfE36POk6yJV_adQsW5c';

describe('workspace cache user isolation', () => {
  beforeEach(() => {
    // Clear localStorage before each test
    if (typeof window !== 'undefined') {
      window.localStorage.clear();
    }
  });

  afterEach(() => {
    // Clean up after each test
    if (typeof window !== 'undefined') {
      window.localStorage.clear();
    }
  });

  it('should use user-scoped cache key based on JWT token', () => {
    if (typeof window === 'undefined') return;

    // Set user A's token
    window.localStorage.setItem('token', USER_A_TOKEN);

    // Write layout for user A
    const userALayout = { ...DEFAULT_LAYOUT, symbol: 'US:AAPL' };
    writeCachedLayout(userALayout);

    // Verify cache key includes user ID
    const cacheKey = `workspace-layout-cache-user-a-id`;
    const cached = window.localStorage.getItem(cacheKey);
    expect(cached).toBeTruthy();
    expect(JSON.parse(cached!)).toEqual(userALayout);
  });

  it('should isolate cache between different users', () => {
    if (typeof window === 'undefined') return;

    // User A logs in and saves layout
    window.localStorage.setItem('token', USER_A_TOKEN);
    const userALayout = { ...DEFAULT_LAYOUT, symbol: 'US:AAPL' };
    writeCachedLayout(userALayout);

    // User A logs out
    window.localStorage.removeItem('token');

    // User B logs in
    window.localStorage.setItem('token', USER_B_TOKEN);

    // User B should not see User A's cached layout
    const cached = readCachedLayout();
    expect(cached).toBeNull(); // No cache for User B yet

    // User B saves their own layout
    const userBLayout = { ...DEFAULT_LAYOUT, symbol: '0700.HK' };
    writeCachedLayout(userBLayout);

    // Verify User B's cache is separate
    const cacheKeyA = `workspace-layout-cache-user-a-id`;
    const cacheKeyB = `workspace-layout-cache-user-b-id`;

    const cachedA = window.localStorage.getItem(cacheKeyA);
    const cachedB = window.localStorage.getItem(cacheKeyB);

    expect(cachedA).toBeTruthy();
    expect(cachedB).toBeTruthy();
    expect(JSON.parse(cachedA!)).toEqual(userALayout);
    expect(JSON.parse(cachedB!)).toEqual(userBLayout);
  });

  it('should clear only current user cache on logout', () => {
    if (typeof window === 'undefined') return;

    // User A logs in and saves layout
    window.localStorage.setItem('token', USER_A_TOKEN);
    const userALayout = { ...DEFAULT_LAYOUT, symbol: 'US:AAPL' };
    writeCachedLayout(userALayout);

    // User A logs out
    clearCachedLayout();
    window.localStorage.removeItem('token');

    // User A's cache should be cleared
    const cacheKeyA = `workspace-layout-cache-user-a-id`;
    const cachedA = window.localStorage.getItem(cacheKeyA);
    expect(cachedA).toBeNull();

    // User B logs in and saves layout
    window.localStorage.setItem('token', USER_B_TOKEN);
    const userBLayout = { ...DEFAULT_LAYOUT, symbol: '0700.HK' };
    writeCachedLayout(userBLayout);

    // User B's cache should exist
    const cacheKeyB = `workspace-layout-cache-user-b-id`;
    const cachedB = window.localStorage.getItem(cacheKeyB);
    expect(cachedB).toBeTruthy();
    expect(JSON.parse(cachedB!)).toEqual(userBLayout);
  });

  it('should handle missing token gracefully', () => {
    if (typeof window === 'undefined') return;

    // No token set
    const cached = readCachedLayout();
    expect(cached).toBeNull();

    // Write should use fallback key (no user ID)
    const layout = { ...DEFAULT_LAYOUT, symbol: 'US:MSFT' };
    writeCachedLayout(layout);

    // Should be stored under fallback key
    const fallbackKey = 'workspace-layout-cache';
    const cachedFallback = window.localStorage.getItem(fallbackKey);
    expect(cachedFallback).toBeTruthy();
  });

  it('should handle malformed JWT token gracefully', () => {
    if (typeof window === 'undefined') return;

    // Set malformed token
    window.localStorage.setItem('token', 'not-a-valid-jwt');

    // Should fall back to default cache key
    const layout = { ...DEFAULT_LAYOUT, symbol: 'US:MSFT' };
    writeCachedLayout(layout);

    const fallbackKey = 'workspace-layout-cache';
    const cached = window.localStorage.getItem(fallbackKey);
    expect(cached).toBeTruthy();
  });
});
