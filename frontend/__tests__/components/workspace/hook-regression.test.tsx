/**
 * Product-level hook regression test — verifies useWorkspaceLayout
 * actually prevents cross-user cache pollution, not just test simulation.
 */

import { renderHook, act } from '@testing-library/react';
import { useWorkspaceLayout } from '@/components/workspace/hooks';
import * as types from '@/components/workspace/types';

// Mock the API and types modules
jest.mock('@/lib/api', () => ({
  api: {
    layouts: {
      getWorkspace: jest.fn().mockResolvedValue({ layout: null }),
      saveWorkspace: jest.fn().mockResolvedValue({}),
    },
  },
}));

jest.mock('@/components/workspace/types', () => ({
  ...jest.requireActual('@/components/workspace/types'),
  readCachedLayout: jest.fn(),
  writeCachedLayout: jest.fn(),
  clearCachedLayout: jest.fn(),
}));

const mockedReadCachedLayout = types.readCachedLayout as jest.MockedFunction<typeof types.readCachedLayout>;
const mockedWriteCachedLayout = types.writeCachedLayout as jest.MockedFunction<typeof types.writeCachedLayout>;

function setUserToken(userId: string | null) {
  if (userId === null) {
    window.localStorage.removeItem('token');
  } else {
    const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const payload = btoa(JSON.stringify({ sub: userId, iat: 1000000 }));
    const signature = 'mock-signature';
    window.localStorage.setItem('token', `${header}.${payload}.${signature}`);
  }
}

describe('useWorkspaceLayout — product-level regression', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    window.localStorage.clear();
    jest.clearAllMocks();
    mockedReadCachedLayout.mockReturnValue(null);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('should NOT write User A debounced save to User B cache after user switch', async () => {
    // User A signs in
    setUserToken('user-a');

    // Mount the actual hook
    const { result } = renderHook(() => useWorkspaceLayout());

    // Wait for initial load
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // User A changes layout — schedules debounced save
    act(() => {
      result.current.setLayout({ symbol: 'AAPL' });
    });

    // Verify writeCachedLayout NOT called yet (debounce pending)
    expect(mockedWriteCachedLayout).not.toHaveBeenCalled();

    // User A signs out, User B signs in BEFORE debounce fires
    setUserToken('user-b');

    // Advance timers — debounce should fire
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // CORRECT: writeCachedLayout should NOT be called with User A's data
    // because the user ID changed between initiation and execution
    const writeCalls = mockedWriteCachedLayout.mock.calls;

    // Either no writes, or all writes are for User B's token
    for (const call of writeCalls) {
      const layout = call[0];
      // If a write happened, it should NOT contain User A's 'AAPL'
      // (unless User B also set it, which they didn't in this test)
      if (layout.symbol === 'AAPL') {
        // This would be a bug — User A's data written under User B's session
        throw new Error('Cross-user cache pollution detected: User A layout written after user switch');
      }
    }
  });

  it('should NOT write User A debounced save after logout', async () => {
    // User A signs in
    setUserToken('user-a');

    // Mount the actual hook
    const { result } = renderHook(() => useWorkspaceLayout());

    // Wait for initial load
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // User A changes layout — schedules debounced save
    act(() => {
      result.current.setLayout({ symbol: 'TSLA' });
    });

    // User A signs out BEFORE debounce fires
    setUserToken(null);

    // Advance timers — debounce should fire
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // CORRECT: writeCachedLayout should NOT be called
    // because there's no valid user session
    const writeCalls = mockedWriteCachedLayout.mock.calls;

    for (const call of writeCalls) {
      const layout = call[0];
      if (layout.symbol === 'TSLA') {
        throw new Error('Stale save detected: User A layout written after logout');
      }
    }
  });

  it('should allow same user to save their own layout', async () => {
    setUserToken('user-a');
    const { result } = renderHook(() => useWorkspaceLayout());

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    act(() => {
      result.current.setLayout({ symbol: 'AAPL' });
    });

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // Verify writeCachedLayout was called with User A's data
    expect(mockedWriteCachedLayout).toHaveBeenCalledWith(
      expect.objectContaining({ symbol: 'AAPL' })
    );
  });

  it('should set hasSavedState when user makes changes', async () => {
    setUserToken('user-a');
    const { result } = renderHook(() => useWorkspaceLayout());

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // Initially no saved state
    expect(result.current.hasSavedState).toBe(false);

    // User makes a change
    act(() => {
      result.current.setLayout({ range: '3M' });
    });

    // hasSavedState should now be true
    expect(result.current.hasSavedState).toBe(true);
  });

  it('should allow User B to save after User A unmounts', async () => {
    // User A session
    setUserToken('user-a');
    const hookA = renderHook(() => useWorkspaceLayout());

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    act(() => {
      hookA.result.current.setLayout({ symbol: 'AAPL' });
    });

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // Unmount User A
    hookA.unmount();

    // User B session
    setUserToken('user-b');
    jest.clearAllMocks();
    mockedReadCachedLayout.mockReturnValue(null);

    const hookB = renderHook(() => useWorkspaceLayout());

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    act(() => {
      hookB.result.current.setLayout({ symbol: 'MSFT' });
    });

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });

    // User B's own save should succeed
    const writeCalls = mockedWriteCachedLayout.mock.calls;
    expect(writeCalls.length).toBeGreaterThan(0);
    const lastCall = writeCalls[writeCalls.length - 1];
    expect(lastCall[0].symbol).toBe('MSFT');
  });
});
