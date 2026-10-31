// Unit coverage for the shared user-facing error helpers.
//
// Run with: node --test tests/userErrors.test.mjs
// These run on Node's built-in test runner and type stripping, so no new
// dependency is added to the frontend package.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  classifyLoginError,
  classifyWriteError,
  parseParameterGridJson,
} from '../src/lib/userErrors.ts';

function apiError(status, message = 'request failed', requestId) {
  const err = new Error(message);
  err.name = 'ApiError';
  err.status = status;
  if (requestId !== undefined) err.requestId = requestId;
  return err;
}

function networkError(message = 'Failed to fetch') {
  return new TypeError(message);
}

test('login: 401 is reported as wrong credentials, not a service problem', () => {
  const result = classifyLoginError(apiError(401, 'Invalid credentials'));
  assert.equal(result.kind, 'invalid_credentials');
  assert.match(result.message, /電郵或密碼不正確/);
  assert.doesNotMatch(result.message, /Invalid credentials|Failed to fetch/);
  assert.equal(result.unconfirmed, false);
});

test('login: 5xx is reported as service unavailable, not wrong credentials', () => {
  const result = classifyLoginError(apiError(503, 'upstream down'));
  assert.equal(result.kind, 'service_unavailable');
  assert.match(result.message, /503/);
  assert.match(result.message, /不是你的帳密問題/);
  assert.doesNotMatch(result.message, /upstream down/);
});

test('login: a server reference number is exposed for quoting, not for retrying', () => {
  const result = classifyLoginError(apiError(503, 'upstream down', 'body-id-123'));
  assert.equal(result.kind, 'service_unavailable');
  assert.equal(result.reference, 'body-id-123');
  assert.match(result.message, /不是你的帳密問題/);
});

test('login: a whitespace-only id is not displayed as a reference', () => {
  const result = classifyLoginError(apiError(500, 'boom', '   '));
  assert.equal(result.reference, undefined);
});

test('login: credential and network failures carry no reference', () => {
  assert.equal(classifyLoginError(apiError(401)).reference, undefined);
  assert.equal(classifyLoginError(networkError()).reference, undefined);
});

test('login: a fetch TypeError is a connection failure', () => {
  const result = classifyLoginError(networkError());
  assert.equal(result.kind, 'network');
  assert.match(result.message, /無法連接登入服務/);
  assert.doesNotMatch(result.message, /Failed to fetch/);
});

test('login: an AbortError is a timeout, still a connection failure', () => {
  const abort = new Error('The operation was aborted');
  abort.name = 'AbortError';
  const result = classifyLoginError(abort);
  assert.equal(result.kind, 'network');
  assert.match(result.message, /逾時/);
});

test('login: 429 is rate limiting, not a credential error', () => {
  const result = classifyLoginError(apiError(429));
  assert.equal(result.kind, 'rate_limited');
  assert.match(result.message, /嘗試次數過多/);
  assert.match(result.message, /不是帳密錯誤/);
});

test('login: 422 surfaces the validation detail', () => {
  const result = classifyLoginError(apiError(422, 'password: too short'));
  assert.equal(result.kind, 'validation');
  assert.match(result.message, /password: too short/);
});

test('login: unknown failures do not echo raw browser text', () => {
  const result = classifyLoginError(new Error('Failed to fetch'));
  assert.equal(result.kind, 'unknown');
  assert.doesNotMatch(result.message, /Failed to fetch/);
});

test('write: 409 is unconfirmed and forbids a blind resubmit', () => {
  const result = classifyWriteError(apiError(409, 'already cancelled'), '取消任務');
  assert.equal(result.kind, 'conflict');
  assert.equal(result.unconfirmed, true);
  assert.match(result.message, /切勿直接重送/);
  assert.match(result.message, /重新載入列表/);
});

test('write: 5xx leaves the outcome unconfirmed', () => {
  const result = classifyWriteError(apiError(502), '建立訓練任務');
  assert.equal(result.kind, 'service_unavailable');
  assert.equal(result.unconfirmed, true);
  assert.match(result.message, /結果未確認/);
  assert.match(result.message, /切勿直接重送/);
});

test('write: a connection failure cannot be treated as a rejection', () => {
  const result = classifyWriteError(networkError('Load failed'), '刪除模型');
  assert.equal(result.kind, 'network');
  assert.equal(result.unconfirmed, true);
  assert.match(result.message, /未確認/);
});

test('write: 400 is a confirmed rejection with the server reason', () => {
  const result = classifyWriteError(apiError(400, 'experiment not found'), '建立訓練任務');
  assert.equal(result.kind, 'validation');
  assert.equal(result.unconfirmed, false);
  assert.match(result.message, /experiment not found/);
});

test('write: 401 asks for a fresh sign-in', () => {
  const result = classifyWriteError(apiError(401), '建立訓練任務');
  assert.equal(result.kind, 'invalid_credentials');
  assert.match(result.message, /重新登入/);
});

test('write: 429 names the quota reason', () => {
  const result = classifyWriteError(apiError(429), '取得最新預測');
  assert.equal(result.kind, 'rate_limited');
  assert.match(result.message, /額度/);
});

test('parameter grid: a valid object becomes the request payload', () => {
  const result = parseParameterGridJson('{"fast_period": [3, 5, 10], "slow_period": [20, 30]}');
  assert.equal(result.ok, true);
  assert.deepEqual(result.value, { fast_period: [3, 5, 10], slow_period: [20, 30] });
});

test('parameter grid: string candidates are accepted', () => {
  const result = parseParameterGridJson('{"strategy": ["sma_crossover", "buy_and_hold"]}');
  assert.equal(result.ok, true);
  assert.deepEqual(result.value.strategy, ['sma_crossover', 'buy_and_hold']);
});

test('parameter grid: a JSON syntax error names line and column', () => {
  // Missing comma between two array elements on line 2.
  const result = parseParameterGridJson('{\n  "fast_period": [3 5]\n}');
  assert.equal(result.ok, false);
  assert.match(result.message, /不是有效的 JSON/);
  assert.match(result.message, /第 2 行/);
});

test('parameter grid: an error without a position still identifies the field', () => {
  // V8 reports trailing commas in arrays without a byte position.
  const result = parseParameterGridJson('{"a": [1,]}');
  assert.equal(result.ok, false);
  assert.match(result.message, /不是有效的 JSON/);
});

test('parameter grid: the root must be an object, not an array', () => {
  const result = parseParameterGridJson('[1, 2, 3]');
  assert.equal(result.ok, false);
  assert.match(result.message, /最外層必須是物件/);
  assert.match(result.message, /陣列/);
});

test('parameter grid: null and primitives are rejected', () => {
  assert.equal(parseParameterGridJson('null').ok, false);
  assert.equal(parseParameterGridJson('42').ok, false);
  assert.equal(parseParameterGridJson('"text"').ok, false);
});

test('parameter grid: every value must be an array', () => {
  const result = parseParameterGridJson('{"fast_period": 3}');
  assert.equal(result.ok, false);
  assert.match(result.message, /參數「fast_period」/);
  assert.match(result.message, /必須是陣列/);
});

test('parameter grid: an empty candidate list is rejected', () => {
  const result = parseParameterGridJson('{"fast_period": []}');
  assert.equal(result.ok, false);
  assert.match(result.message, /不可為空/);
});

test('parameter grid: an empty object is rejected', () => {
  const result = parseParameterGridJson('{}');
  assert.equal(result.ok, false);
  assert.match(result.message, /至少要包含一個參數/);
});

test('parameter grid: blank input is rejected before any request', () => {
  const result = parseParameterGridJson('   \n  ');
  assert.equal(result.ok, false);
  assert.match(result.message, /不可為空/);
});

test('parameter grid: an empty parameter name is rejected', () => {
  const result = parseParameterGridJson('{"": [1]}');
  assert.equal(result.ok, false);
  assert.match(result.message, /參數名稱不可為空/);
});
