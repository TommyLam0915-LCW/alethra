'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/lib/api';

interface AIStatus {
  provider: string;
  model: string;
  mode: string;
  api_key_configured: boolean;
  advisory_notice: string;
}

interface AIMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  status: string;
  error_detail?: string | null;
  provider?: string | null;
  model?: string | null;
  prompt_tokens?: number;
  completion_tokens?: number;
  quota_used?: number;
  created_at: string;
}

interface AIConversationSummary {
  id: string;
  title: string;
  context: { research_draft_ids?: string[]; memory_ids?: string[]; instrument_symbols?: string[] };
  message_count: number;
  updated_at: string;
}

interface DraftOption {
  id: string;
  title: string;
}

interface MemoryOption {
  id: string;
  content: string;
  privacy_scope: string;
}

const ACTIVE_CONVERSATION_KEY = 'alethra.ai.activeConversationId';

type ContextKind = 'research_draft_ids' | 'memory_ids';

export default function AIPanel({ currentSymbol }: { currentSymbol: string }) {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [conversations, setConversations] = useState<AIConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AIMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [panelError, setPanelError] = useState('');
  const [quotaUsed, setQuotaUsed] = useState<number | null>(null);
  const [connResult, setConnResult] = useState<string>('');
  const [ctxOpen, setCtxOpen] = useState(false);
  const [draftOptions, setDraftOptions] = useState<DraftOption[]>([]);
  const [memoryOptions, setMemoryOptions] = useState<MemoryOption[]>([]);
  const [ctxSelection, setCtxSelection] = useState<{ research_draft_ids: string[]; memory_ids: string[] }>({
    research_draft_ids: [],
    memory_ids: [],
  });
  const [draftFormFor, setDraftFormFor] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [draftHypothesis, setDraftHypothesis] = useState('');
  const [draftSaved, setDraftSaved] = useState('');
  const threadRef = useRef<HTMLDivElement>(null);

  const activeConversation = conversations.find((c) => c.id === activeId) || null;

  const loadConversations = useCallback(async () => {
    try {
      const data = await api.aiAssistant.listConversations();
      const list = Array.isArray(data) ? data : [];
      setConversations(list);
      setActiveId((prev) => {
        // Restore the thread the user was reading before a reload, then the
        // already-selected one, and only then fall back to the newest thread.
        const stored = typeof window !== 'undefined' ? window.localStorage.getItem(ACTIVE_CONVERSATION_KEY) : null;
        for (const candidate of [prev, stored]) {
          if (candidate && list.some((c) => c.id === candidate)) return candidate;
        }
        return list[0]?.id ?? null;
      });
    } catch {
      setConversations([]);
    }
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (activeId) window.localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeId);
    else window.localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
  }, [activeId]);

  // Seed the context checkboxes from the server when the thread changes. While
  // the thread stays open the local selection is authoritative so two quick
  // checkbox clicks cannot overwrite each other with a stale copy.
  useEffect(() => {
    const conv = conversations.find((c) => c.id === activeId);
    setCtxSelection({
      research_draft_ids: conv?.context.research_draft_ids || [],
      memory_ids: conv?.context.memory_ids || [],
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.aiAssistant.status());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    loadConversations();
  }, [loadStatus, loadConversations]);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await api.aiAssistant.getConversation(activeId);
        if (!cancelled) setMessages(data.messages || []);
      } catch {
        if (!cancelled) setMessages([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages]);

  const newConversation = async () => {
    try {
      let created;
      try {
        created = await api.aiAssistant.createConversation(
          currentSymbol ? { context: { instrument_symbols: [currentSymbol] } } : {}
        );
      } catch (err) {
        // A saved layout symbol can outlive the instrument catalogue. The thread
        // itself is still usable, so create it without the unknown symbol and
        // say which context was dropped instead of blocking the + button.
        const unknownSymbol =
          err instanceof ApiError && err.status === 400 && /instrument/i.test(err.message);
        if (!unknownSymbol) throw err;
        created = await api.aiAssistant.createConversation({});
        setPanelError(`目前標的 ${currentSymbol} 不在可辨識的清單，已建立不含標的脈絡的對話。`);
      }
      await loadConversations();
      setActiveId(created.id);
      setMessages([]);
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : '無法建立對話');
    }
  };

  const deleteConversation = async () => {
    if (!activeId) return;
    try {
      await api.aiAssistant.deleteConversation(activeId);
      await loadConversations();
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : '無法刪除對話');
    }
  };

  const addCurrentSymbol = async () => {
    if (!activeConversation) return;
    const symbols = activeConversation.context.instrument_symbols || [];
    if (symbols.includes(currentSymbol)) return;
    try {
      await api.aiAssistant.updateConversation(activeConversation.id, {
        context: {
          research_draft_ids: activeConversation.context.research_draft_ids || [],
          memory_ids: activeConversation.context.memory_ids || [],
          instrument_symbols: [...symbols, currentSymbol],
        },
      });
      await loadConversations();
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : '無法更新脈絡');
    }
  };

  const openContextEditor = async () => {
    setCtxOpen((v) => !v);
    if (draftOptions.length === 0) {
      try {
        const drafts = await api.researchDrafts.list();
        setDraftOptions((Array.isArray(drafts) ? drafts : []).map((d: DraftOption) => ({ id: d.id, title: d.title })));
      } catch {
        setDraftOptions([]);
      }
    }
    if (memoryOptions.length === 0) {
      try {
        const memories = await api.personalMemory.list();
        setMemoryOptions(
          (Array.isArray(memories) ? memories : [])
            .filter((m: MemoryOption) => m.privacy_scope === 'approved_for_personal_ai')
            .map((m: MemoryOption) => ({ id: m.id, content: m.content, privacy_scope: m.privacy_scope }))
        );
      } catch {
        setMemoryOptions([]);
      }
    }
  };

  const toggleContext = async (kind: ContextKind, id: string) => {
    if (!activeConversation) return;
    const current = ctxSelection[kind];
    const nextSelection = {
      ...ctxSelection,
      [kind]: current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
    };
    setCtxSelection(nextSelection);
    try {
      await api.aiAssistant.updateConversation(activeConversation.id, {
        context: {
          research_draft_ids: nextSelection.research_draft_ids,
          memory_ids: nextSelection.memory_ids,
          instrument_symbols: activeConversation.context.instrument_symbols || [],
        },
      });
      await loadConversations();
    } catch (err) {
      setCtxSelection(ctxSelection);
      setPanelError(err instanceof Error ? err.message : '無法更新脈絡');
    }
  };

  const send = async () => {
    const content = input.trim();
    if (!content || !activeId || sending) return;
    const optimisticId = `local-${Date.now()}`;
    setSending(true);
    setPanelError('');
    setMessages((prev) => [
      ...prev,
      { id: optimisticId, role: 'user', content, status: 'completed', created_at: new Date().toISOString() },
    ]);
    setInput('');
    try {
      const reply = await api.aiAssistant.sendMessage(activeId, content);
      setMessages((prev) => [...prev, reply]);
      if (typeof reply.quota_used === 'number') setQuotaUsed(reply.quota_used);
      await loadConversations();
    } catch (err) {
      // The turn was refused (quota/entitlement/network): drop the optimistic
      // bubble and put the text back so the user does not lose what they typed.
      setMessages((prev) => prev.filter((m) => m.id !== optimisticId));
      setInput((current) => (current ? current : content));
      setPanelError(err instanceof Error ? err.message : '傳送失敗');
      loadConversations();
    } finally {
      setSending(false);
    }
  };

  const testConnection = async () => {
    setConnResult('測試中…');
    try {
      const result = await api.aiAssistant.testConnection();
      setConnResult(result.ok ? `✓ ${result.detail}` : `✗ ${result.detail}`);
    } catch (err) {
      setConnResult(err instanceof Error ? `✗ ${err.message}` : '✗ 測試失敗');
    }
  };

  const saveDraft = async () => {
    if (!activeId || !draftFormFor || !draftTitle.trim()) return;
    try {
      const draft = await api.aiAssistant.createDraft(activeId, {
        title: draftTitle.trim(),
        hypothesis: draftHypothesis.trim() || null,
        source_message_id: draftFormFor,
      });
      setDraftSaved(`已建立待審核草稿 ${draft.id.slice(0, 8)}…`);
      setDraftFormFor(null);
      setDraftTitle('');
      setDraftHypothesis('');
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : '儲存草稿失敗');
    }
  };

  const ctxSymbols = activeConversation?.context.instrument_symbols || [];
  const ctxDraftCount = ctxSelection.research_draft_ids.length;
  const ctxMemoryCount = ctxSelection.memory_ids.length;

  return (
    <div className="ws-ai-panel" data-testid="ai-panel">
      <div className="ws-ai-headrow">
        <span className="ws-ai-title">🤖 個人 AI 助手</span>
        {status && (
          <span className={`ws-badge ${status.mode === 'live' ? 'ws-badge-live' : ''}`} data-testid="ai-mode-badge">
            {status.mode === 'live' ? `AI 即時（${status.provider}）` : 'AI 模擬'}
          </span>
        )}
        <button className="ws-icon-btn" title="測試供應商連線" onClick={testConnection} data-testid="ai-test-connection">
          測試
        </button>
      </div>
      {connResult && <div className="ws-ai-conn">{connResult}</div>}

      <div className="ws-ai-conv-row">
        <select
          className="ws-ai-select"
          value={activeId ?? ''}
          onChange={(e) => setActiveId(e.target.value || null)}
          aria-label="選擇 AI 對話"
          data-testid="ai-conversation-select"
        >
          {conversations.length === 0 && <option value="">（尚無對話）</option>}
          {conversations.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button
          className="ws-icon-btn"
          title="新對話"
          aria-label="新對話"
          onClick={newConversation}
          data-testid="ai-new-conversation"
          style={{ whiteSpace: 'nowrap' }}
        >
          ＋ 新對話
        </button>
        <button
          className="ws-icon-btn"
          title="刪除此對話"
          aria-label="刪除此對話"
          onClick={deleteConversation}
          disabled={!activeId}
        >
          ✕
        </button>
      </div>

      {conversations.length === 0 && (
        <div className="ws-muted" data-testid="ai-conversation-hint">
          尚無對話：按「＋ 新對話」建立第一個研究對話。
        </div>
      )}

      <div className="ws-ai-context">
        {ctxSymbols.map((s) => (
          <span key={s} className="ws-chip">{s}</span>
        ))}
        {ctxDraftCount > 0 && <span className="ws-chip">草稿×{ctxDraftCount}</span>}
        {ctxMemoryCount > 0 && <span className="ws-chip">記憶×{ctxMemoryCount}</span>}
        {currentSymbol && (
          <button
            className="ws-icon-btn"
            title={`把目前標的 ${currentSymbol} 加入 AI 脈絡`}
            aria-label={`把目前標的 ${currentSymbol} 加入 AI 脈絡`}
            onClick={addCurrentSymbol}
            disabled={!activeId}
          >
            ＋ {currentSymbol}
          </button>
        )}
        <button
          className="ws-icon-btn"
          title="選擇要提供給 AI 的研究草稿與個人記憶"
          aria-label="編輯研究脈絡"
          onClick={openContextEditor}
          disabled={!activeId}
        >
          脈絡
        </button>
      </div>

      {ctxOpen && activeConversation && (
        <div className="ws-ai-ctx-editor" data-testid="ai-context-editor">
          <div className="ws-ai-ctx-section">研究草稿</div>
          {draftOptions.length === 0 && <div className="ws-muted">沒有可選草稿</div>}
          {draftOptions.slice(0, 8).map((d) => (
            <label key={d.id} className="ws-ai-ctx-item">
              <input
                type="checkbox"
                checked={ctxSelection.research_draft_ids.includes(d.id)}
                onChange={() => toggleContext('research_draft_ids', d.id)}
              />
              <span className="ws-ai-ctx-label">{d.title}</span>
            </label>
          ))}
          <div className="ws-ai-ctx-section">已授權個人記憶</div>
          {memoryOptions.length === 0 && <div className="ws-muted">沒有已授權的記憶</div>}
          {memoryOptions.slice(0, 8).map((m) => (
            <label key={m.id} className="ws-ai-ctx-item">
              <input
                type="checkbox"
                checked={ctxSelection.memory_ids.includes(m.id)}
                onChange={() => toggleContext('memory_ids', m.id)}
              />
              <span className="ws-ai-ctx-label">{m.content.slice(0, 40)}</span>
            </label>
          ))}
        </div>
      )}

      <div className="ws-ai-advisory">⚠ AI 回覆僅供研究參考，不構成交易授權</div>

      <div className="ws-ai-thread" ref={threadRef} data-testid="ai-thread">
        {messages.length === 0 && (
          <div className="ws-muted ws-ai-empty">
            按「＋ 新對話」建立或選擇一段對話；再用「脈絡」加入研究草稿、記憶與標的。
            （當前供應商：{status?.provider ?? '未知'}）
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`ws-ai-msg ${m.role === 'user' ? 'ws-ai-msg-user' : 'ws-ai-msg-assistant'} ${m.status === 'error' ? 'ws-ai-msg-error' : ''}`}>
            <div className="ws-ai-msg-content">{m.content}</div>
            {m.status === 'error' && m.error_detail && <div className="ws-ai-errdetail">{m.error_detail}</div>}
            {m.role === 'assistant' && m.status === 'completed' && (
              <div className="ws-ai-msg-actions">
                <button
                  className="ws-icon-btn"
                  onClick={() => {
                    setDraftFormFor(m.id);
                    setDraftTitle(`AI 提議：${m.content.slice(0, 30)}`);
                    setDraftHypothesis(m.content);
                    setDraftSaved('');
                  }}
                  data-testid="ai-save-draft"
                >
                  儲存為研究草稿
                </button>
                <span className="ws-muted">{m.completion_tokens ?? 0} tokens</span>
              </div>
            )}
          </div>
        ))}
        {draftFormFor && (
          <div className="ws-ai-draft-form" data-testid="ai-draft-form">
            <input
              className="ws-input"
              value={draftTitle}
              onChange={(e) => setDraftTitle(e.target.value)}
              placeholder="草稿標題"
            />
            <textarea
              className="ws-input"
              value={draftHypothesis}
              onChange={(e) => setDraftHypothesis(e.target.value)}
              placeholder="研究假設／AI 提議內容"
              rows={4}
              style={{ marginTop: 8, resize: 'vertical' }}
            />
            <div className="ws-ai-draft-form-actions">
              <button className="btn btn-primary" onClick={saveDraft} disabled={!draftTitle.trim()}>
                建立待審核草稿
              </button>
              <button className="ws-icon-btn" onClick={() => setDraftFormFor(null)}>
                取消
              </button>
            </div>
          </div>
        )}
        {draftSaved && <div className="ws-ai-saved">{draftSaved}（待人工審核）</div>}
      </div>

      {panelError && <div className="ws-message-error">{panelError}</div>}

      <div className="ws-ai-input-row">
        <textarea
          className="ws-ai-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="向 AI 助手提問研究問題…"
          rows={2}
          data-testid="ai-input"
        />
        <button className="btn btn-primary" onClick={send} disabled={sending || !activeId || !input.trim()} data-testid="ai-send">
          {sending ? '傳送中…' : '傳送'}
        </button>
      </div>
      {quotaUsed !== null && <div className="ws-muted ws-ai-quota">AI 用量：{quotaUsed}</div>}
    </div>
  );
}
