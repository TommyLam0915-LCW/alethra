// Shared classification for user-facing failures.
//
// The pages used to collapse three different events into one raw `err.message`:
// "wrong password" (401), "the service is down" (5xx, or no listener at all →
// TypeError) and "this input is not valid JSON". Each needs a different next
// action, so the classification lives here. It has no runtime imports, which
// keeps it usable from Node's type-stripping test runner without a bundler.

export type FailureKind =
  | 'invalid_credentials'
  | 'forbidden'
  | 'rate_limited'
  | 'validation'
  | 'conflict'
  | 'service_unavailable'
  | 'network'
  | 'unknown';

export interface ClassifiedFailure {
  kind: FailureKind;
  /** Traditional Chinese message shown in the page; never a raw backend string. */
  message: string;
  /**
   * True when the request may have reached the server but no confirmation came
   * back. Callers must tell the user to reload the list instead of resubmitting.
   */
  unconfirmed: boolean;
  /**
   * Server-generated id identifying this exact failed request in the server
   * log. Present only when the response supplied one (server errors). This is a
   * value to quote back to an operator; it is never used to retry anything.
   */
  reference?: string;
}

function statusOf(err: unknown): number | null {
  if (
    err !== null &&
    typeof err === 'object' &&
    typeof (err as { status?: unknown }).status === 'number'
  ) {
    return (err as { status: number }).status;
  }
  return null;
}

function messageOf(err: unknown): string {
  if (err !== null && typeof err === 'object') {
    const message = (err as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message.trim();
  }
  if (typeof err === 'string' && err.trim()) return err.trim();
  return '';
}

function referenceOf(err: unknown): string | undefined {
  if (err !== null && typeof err === 'object') {
    const value = (err as { requestId?: unknown }).requestId;
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return undefined;
}

function errorNameOf(err: unknown): string {
  if (err !== null && typeof err === 'object') {
    const name = (err as { name?: unknown }).name;
    if (typeof name === 'string') return name;
  }
  return '';
}

// `fetch` rejects with a TypeError when the request never reached the server
// (no listener, DNS failure, offline, CORS refusal). An AbortError is the
// caller's own timeout. Neither carries an HTTP status.
function isNetworkError(err: unknown): boolean {
  if (statusOf(err) !== null) return false;
  const name = errorNameOf(err);
  return name === 'TypeError' || name === 'AbortError';
}

export function classifyLoginError(err: unknown): ClassifiedFailure {
  const status = statusOf(err);
  if (status === 401) {
    return {
      kind: 'invalid_credentials',
      message: '電郵或密碼不正確。請檢查後再試，已輸入的內容會保留。',
      unconfirmed: false,
    };
  }
  if (status === 429) {
    return {
      kind: 'rate_limited',
      message:
        '嘗試次數過多，登入暫時受限。請稍候約一分鐘再試；這不是帳密錯誤。',
      unconfirmed: false,
    };
  }
  if (status === 403) {
    return {
      kind: 'forbidden',
      message: '此帳戶目前無法登入。請聯絡管理員確認帳戶狀態。',
      unconfirmed: false,
    };
  }
  if (status !== null && status >= 500) {
    return {
      kind: 'service_unavailable',
      message: `登入服務暫時無法使用（伺服器回應 ${status}）。這不是你的帳密問題，請稍後再試；輸入內容已保留。`,
      unconfirmed: false,
      reference: referenceOf(err),
    };
  }
  if (isNetworkError(err)) {
    const timedOut = errorNameOf(err) === 'AbortError';
    return {
      kind: 'network',
      message: timedOut
        ? '登入請求逾時，未收到伺服器回應。請確認網絡後再試；輸入內容已保留。'
        : '無法連接登入服務（網絡中斷或服務未啟動）。這不是帳密錯誤，請確認網絡後再試；輸入內容已保留。',
      unconfirmed: false,
    };
  }
  if (status === 400 || status === 422) {
    const detail = messageOf(err);
    return {
      kind: 'validation',
      message: detail ? `登入資料格式有誤：${detail}` : '登入資料格式有誤，請檢查電郵與密碼格式。',
      unconfirmed: false,
    };
  }
  return {
    kind: 'unknown',
    message: '登入時發生未預期的錯誤，請稍後再試。',
    unconfirmed: false,
  };
}

/**
 * Classify the outcome of a write (create/cancel/predict/delete).
 *
 * `action` is a Chinese noun phrase that fits both templates below, e.g.
 * 「建立訓練任務」 or 「刪除模型」.
 */
export function classifyWriteError(err: unknown, action: string): ClassifiedFailure {
  const status = statusOf(err);
  const detail = messageOf(err);
  if (status === 401) {
    return {
      kind: 'invalid_credentials',
      message: `登入狀態已失效，${action}未有執行。請重新登入後再試。`,
      unconfirmed: false,
    };
  }
  if (status === 403) {
    return {
      kind: 'forbidden',
      message: `權限不足，${action}被拒絕（403）${detail ? `：${detail}` : '。'}`,
      unconfirmed: false,
    };
  }
  if (status === 409) {
    return {
      kind: 'conflict',
      message: `伺服器回報狀態衝突${detail ? `：${detail}` : ''}。請先重新載入列表確認目前狀態，切勿直接重送。`,
      unconfirmed: true,
    };
  }
  if (status === 429) {
    return {
      kind: 'rate_limited',
      message: `已達使用額度，${action}被拒絕（429）。請先查看額度，再決定是否再試。`,
      unconfirmed: false,
    };
  }
  if (status !== null && status >= 500) {
    return {
      kind: 'service_unavailable',
      message: `伺服器暫時無法處理${action}（${status}），結果未確認。請重新載入列表確認是否已生效，切勿直接重送。`,
      unconfirmed: true,
    };
  }
  if (status === 400 || status === 422) {
    return {
      kind: 'validation',
      message: `${action}被伺服器拒絕${detail ? `：${detail}` : '，請檢查輸入後再試。'}`,
      unconfirmed: false,
    };
  }
  if (isNetworkError(err)) {
    return {
      kind: 'network',
      message: `無法連接伺服器，${action}的結果未確認（請求可能已送出，但未收到回應）。請重新載入列表確認，切勿直接重送。`,
      unconfirmed: true,
    };
  }
  return {
    kind: 'unknown',
    message: `未能確認${action}是否成功${detail ? `（${detail}）` : ''}。請重新載入列表確認，切勿直接重送。`,
    unconfirmed: true,
  };
}

export type ParameterGridParseResult =
  | { ok: true; value: Record<string, unknown[]> }
  | { ok: false; message: string };

const PARAM_GRID_EXAMPLE = '{"fast_period": [3, 5, 10], "slow_period": [20, 30]}';

function describeJsonType(value: unknown): string {
  if (value === null) return 'null';
  if (Array.isArray(value)) return '陣列';
  switch (typeof value) {
    case 'number':
      return '數字';
    case 'string':
      return '字串';
    case 'boolean':
      return '布林值';
    case 'object':
      return '物件';
    default:
      return '未知類型';
  }
}

// V8 reports `at position N`, Firefox/Node's newer parser reports
// `line L column C`, and Safari reports neither. Convert a byte position into
// a 1-based line/column the user can find in the textarea.
function locateJsonError(err: unknown, text: string): { line: number; column: number } | null {
  const message = messageOf(err);
  const positionMatch = /at position (\d+)/i.exec(message);
  if (positionMatch) {
    const position = Math.min(Number(positionMatch[1]), text.length);
    const before = text.slice(0, position);
    const line = before.split('\n').length;
    const lastNewline = before.lastIndexOf('\n');
    return { line, column: position - lastNewline };
  }
  const lineColumnMatch = /line (\d+) column (\d+)/i.exec(message);
  if (lineColumnMatch) {
    return { line: Number(lineColumnMatch[1]), column: Number(lineColumnMatch[2]) };
  }
  return null;
}

/**
 * Validate the parameter-grid textarea before any request is sent, so a JSON
 * typo is reported at the field instead of surfacing as "creation failed".
 */
export function parseParameterGridJson(raw: string): ParameterGridParseResult {
  const text = (raw || '').trim();
  if (!text) {
    return {
      ok: false,
      message: `參數網格不可為空。請輸入 JSON 物件，例如 ${PARAM_GRID_EXAMPLE}。`,
    };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    const location = locateJsonError(err, text);
    const where = location ? `第 ${location.line} 行第 ${location.column} 列` : '格式錯誤處';
    return {
      ok: false,
      message: `參數網格不是有效的 JSON（${where}）。請檢查該處的引號、逗號與括號；表單內容已保留。`,
    };
  }

  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {
      ok: false,
      message: `參數網格的最外層必須是物件 { }，不可使用${describeJsonType(parsed)}。`,
    };
  }

  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.length === 0) {
    return {
      ok: false,
      message: '參數網格至少要包含一個參數，例如 {"fast_period": [3, 5, 10]}。',
    };
  }

  const value: Record<string, unknown[]> = {};
  for (const [name, values] of entries) {
    if (!name.trim()) {
      return { ok: false, message: '參數名稱不可為空。' };
    }
    if (!Array.isArray(values)) {
      return {
        ok: false,
        message: `參數「${name}」的值必須是陣列，例如 [3, 5, 10]；目前是${describeJsonType(values)}。`,
      };
    }
    if (values.length === 0) {
      return {
        ok: false,
        message: `參數「${name}」的陣列不可為空，至少要有一個候選值。`,
      };
    }
    value[name] = values;
  }
  return { ok: true, value };
}
