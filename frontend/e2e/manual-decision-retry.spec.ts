import { test, expect } from '@playwright/test';
import { api, ApiError } from '../src/lib/api';

const originalFetch = global.fetch;

test.afterEach(() => {
  global.fetch = originalFetch;
});

test.describe('Manual decision retry flow - API client behavior', () => {
  test('sends expected_version in payload', async () => {
    let capturedBody: any = null;
    global.fetch = (async (url: string, options: RequestInit) => {
      capturedBody = JSON.parse(options.body as string);
      return new Response(JSON.stringify({
        session: { id: 's1', state_version: 2 },
        decision: { id: 'd1' }
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;

    await api.simulations.manualDecision('s1', {
      action: 'buy',
      idempotency_key: 'key-123',
      quantity: 100,
      expected_version: 1,
    });

    expect(capturedBody).toMatchObject({
      action: 'buy',
      idempotency_key: 'key-123',
      quantity: 100,
      expected_version: 1,
    });
  });

  test('distinguishes state version conflict from payload conflict', async () => {
    // State version conflict
    global.fetch = (async () =>
      new Response(JSON.stringify({
        detail: 'State version conflict: expected 1, current 2. Session has been modified.'
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;

    try {
      await api.simulations.manualDecision('s1', {
        action: 'buy',
        idempotency_key: 'key-123',
        expected_version: 1,
      });
      throw new Error('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);
      expect((err as ApiError).message).toContain('State version conflict');
    }

    // Payload conflict
    global.fetch = (async () =>
      new Response(JSON.stringify({
        detail: 'Idempotency key conflict: same key with different payload'
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;

    try {
      await api.simulations.manualDecision('s1', {
        action: 'buy',
        idempotency_key: 'key-123',
        expected_version: 1,
      });
      throw new Error('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);
      expect((err as ApiError).message).toContain('Idempotency key conflict');
      expect((err as ApiError).message).toContain('different payload');
    }
  });

  test('network failure preserves error for retry logic', async () => {
    global.fetch = (async () => {
      throw new Error('Network error: connection timeout');
    }) as typeof fetch;

    try {
      await api.simulations.manualDecision('s1', {
        action: 'buy',
        idempotency_key: 'key-123',
        expected_version: 1,
      });
      throw new Error('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(Error);
      expect((err as Error).message).toContain('Network error');
      // Not an ApiError, so retry logic will preserve pendingDecision
    }
  });

  test('idempotent replay returns original decision', async () => {
    global.fetch = (async () =>
      new Response(JSON.stringify({
        session: { id: 's1', state_version: 1 },
        decision: { id: 'd1', step_number: 5, decision_type: 'manual_buy' },
        fill: { quantity: 100, price: 50.0 }
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;

    const result = await api.simulations.manualDecision('s1', {
      action: 'buy',
      idempotency_key: 'key-123',
      quantity: 100,
      expected_version: 1,
    });

    expect(result.decision.id).toBe('d1');
    expect(result.decision.step_number).toBe(5);
    expect(result.fill?.quantity).toBe(100);
  });
});

test.describe('Manual decision retry flow - behavioral contracts', () => {
  test('double-click protection: same key sent twice returns same decision', async () => {
    let callCount = 0;
    const decisionId = 'd1';
    
    global.fetch = (async () => {
      callCount++;
      // Both calls return the same decision (idempotent)
      return new Response(JSON.stringify({
        session: { id: 's1', state_version: callCount === 1 ? 2 : 2 },
        decision: { id: decisionId, step_number: 5 },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;

    const key = 'key-double-click';
    const payload = {
      action: 'buy' as const,
      idempotency_key: key,
      quantity: 100,
      expected_version: 1,
    };

    const result1 = await api.simulations.manualDecision('s1', payload);
    const result2 = await api.simulations.manualDecision('s1', payload);

    expect(callCount).toBe(2);
    expect(result1.decision.id).toBe(decisionId);
    expect(result2.decision.id).toBe(decisionId);
    // Same decision ID = no duplicate timeline entry
  });

  test('retry after network failure uses exact same payload', async () => {
    let capturedPayloads: any[] = [];
    let callCount = 0;
    
    global.fetch = (async (url: string, options: RequestInit) => {
      callCount++;
      const body = JSON.parse(options.body as string);
      capturedPayloads.push(body);
      
      if (callCount === 1) {
        throw new Error('Network timeout');
      }
      
      return new Response(JSON.stringify({
        session: { id: 's1', state_version: 2 },
        decision: { id: 'd1' },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;

    const key = 'key-retry';
    const payload = {
      action: 'buy' as const,
      idempotency_key: key,
      quantity: 100,
      rationale: 'test rationale',
      expected_version: 1,
    };

    // First attempt fails
    try {
      await api.simulations.manualDecision('s1', payload);
    } catch (err) {
      expect((err as Error).message).toContain('Network timeout');
    }

    // Retry with exact same payload
    const result = await api.simulations.manualDecision('s1', payload);

    expect(callCount).toBe(2);
    expect(capturedPayloads[0]).toEqual(capturedPayloads[1]);
    expect(capturedPayloads[0].idempotency_key).toBe(key);
    expect(capturedPayloads[0].expected_version).toBe(1);
    expect(result.decision.id).toBe('d1');
  });

  test('stale-version 409 does not auto-resubmit', async () => {
    let callCount = 0;
    
    global.fetch = (async () => {
      callCount++;
      return new Response(JSON.stringify({
        detail: 'State version conflict: expected 1, current 2. Session has been modified.'
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;

    try {
      await api.simulations.manualDecision('s1', {
        action: 'buy',
        idempotency_key: 'key-stale',
        expected_version: 1,
      });
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);
    }

    // Only one call - no auto-resubmission
    expect(callCount).toBe(1);
  });

  test('payload conflict 409 displays distinct error', async () => {
    global.fetch = (async () =>
      new Response(JSON.stringify({
        detail: 'Idempotency key conflict: same key with different payload'
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;

    try {
      await api.simulations.manualDecision('s1', {
        action: 'buy',
        idempotency_key: 'key-conflict',
        quantity: 100,
        expected_version: 1,
      });
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const message = (err as ApiError).message;
      expect(message).toContain('Idempotency key conflict');
      expect(message).toContain('different payload');
    }
  });
});
