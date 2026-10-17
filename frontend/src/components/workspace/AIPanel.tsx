'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

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
  const [draftFormFor, setDraftFormFor] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [draftSaved, setDraftSaved] = useState('');
  const threadRef = useRef<HTMLDivElement>(null);

  const activeConversation = conversations.find((c) => c.id === activeId) || null;

  const loadConversations = useCallback(async () => {
    try {
      const data = await api.aiAssistant.listConversations();
      const list = Array.isArray(data) ? data : [];
      setConversations(list);
      setActiveId((prev) => (prev && list.some((c) => c.id === prev) ? prev : list[0]?.id ?? null));
    } catch {
      setConversations([]);
    }
  }, []);

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
      const created = await api.aiAssistant.createConversation({ context: { instrument_symbols: [currentSymbol] } });
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

  const toggleContextId = (kind: 'research_draft_ids' | 'memory_ids', id: string) => {
    if (!activeConversation) return;
    const current = activeConversation.context[kind] || [];
    return current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
  };

  const saveContext = async (kind: 'research_draft_ids' | 'memory_ids', ids: string[]) => {
    if (!activeConversation) return;
    try {
      await api.aiAssistant.updateConversation(activeConversation.id, {
        context: {
          research_draft_ids: kind === 'research_draft_ids' ? ids : activeConversation.context.research_draft_ids || [],
          memory_ids: kind === 'memory_ids' ? ids : activeConversation.context.memory_ids || [],
          instrument_symbols: activeConversation.context.instrument_symbols || [],
        },
      });
      await loadConversations();
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : '無法更新脈絡');
    }
  };

  const send = async () => {
    const content = input.trim();
    if (!content || !activeId || sending) return;
    setSending(true);
    setPanelError('');
    setMessages((prev) => [
      ...prev,
      { id: `local-${Date.now()}`, role: 'user', content, status: 'completed', created_at: new Date().toISOString() },
    ]);
    setInput('');
    try {
      const reply = await api.aiAssistant.sendMessage(activeId, content);
      setMessages((prev) => [...prev, reply]);
      if (typeof reply.quota_used === 'number') setQuotaUsed(reply.quota_used);
      await loadConversations();
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : '傳送失敗');
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
        source_message_id: draftFormFor,
      });
      setDraftSaved(`已建立待審核草稿 ${draft.id.slice(0, 8)}…`);
      setDraftFormFor(null);
      setDraftTitle('');
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : '儲存草稿失敗');
    }
  };

  const ctxSymbols = activeConversation?.context.instrument_symbols || [];
  const ctxDraftCount = activeConversation?.context.research_draft_ids?.length || 0;
  const ctxMemoryCount = activeConversation?.context.memory_ids?.length || 0;

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
          data-testid="ai-conversation-select"
        >
          {conversations.length === 0 && <option value="">（尚無對話）</option>}
          {conversations.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button className="ws-icon-btn" title="新對話" onClick={newConversation} data-testid="ai-new-conversation">
          ＋
        </button>
        <button className="ws-icon-btn" title="刪除此對話" onClick={deleteConversation} disabled={!activeId}>
          ✕
        </button>
      </div>

      <div className="ws-ai-context">
        {ctxSymbols.map((s) => (
          <span key={s} className="ws-chip">{s}</span>
        ))}
        {ctxDraftCount > 0 && <span className="ws-chip">草稿×{ctxDraftCount}</span>}
        {ctxMemoryCount > 0 && <span className="ws-chip">記憶×{ctxMemoryCount}</span>}
        <button className="ws-icon-btn" title="把目前工具加入 AI 脈絡" onClick={addCurrentSymbol} disabled={!activeId}>
          ＋{currentSymbol}
        </button>
        <button className="ws-icon-btn" title="編輯研究脈絡" onClick={openContextEditor} disabled={!activeId}>
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
                checked={(activeConversation.context.research_draft_ids || []).includes(d.id)}
                onChange={() => saveContext('research_draft_ids', toggleContextId('research_draft_ids', d.id) || [])}
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
                checked={(activeConversation.context.memory_ids || []).includes(m.id)}
                onChange={() => saveContext('memory_ids', toggleContextId('memory_ids', m.id) || [])}
              />
              <span className="ws-ai-ctx-label">{m.content.slice(0, 40)}</span>
            </label>
          ))}
        </div>
      )}

      <div className="ws-ai-advisory">⚠ AI 回覆僅供研究參考，不構成交易授權</div>

      <div className="ws-ai-thread" ref={threadRef} data-testid="ai-thread">
        {messages.length === 0 && <div className="ws-muted ws-ai-empty">開始一段研究對話（當前供應商：{status?.provider ?? '未知'}）</div>}
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
