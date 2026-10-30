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
        setPanelError(
          `The current symbol ${currentSymbol} is not in the recognised list, so the conversation was created without a symbol context.`,
        );
      }
      await loadConversations();
      setActiveId(created.id);
      setMessages([]);
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : 'Could not create the conversation');
    }
  };

  const deleteConversation = async () => {
    if (!activeId) return;
    try {
      await api.aiAssistant.deleteConversation(activeId);
      await loadConversations();
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : 'Could not delete the conversation');
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
      setPanelError(err instanceof Error ? err.message : 'Could not update the context');
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
      setPanelError(err instanceof Error ? err.message : 'Could not update the context');
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
      setPanelError(err instanceof Error ? err.message : 'Send failed');
      loadConversations();
    } finally {
      setSending(false);
    }
  };

  const testConnection = async () => {
    setConnResult('Testing…');
    try {
      const result = await api.aiAssistant.testConnection();
      setConnResult(result.ok ? `✓ ${result.detail}` : `✗ ${result.detail}`);
    } catch (err) {
      setConnResult(err instanceof Error ? `✗ ${err.message}` : '✗ Test failed');
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
      setDraftSaved(`Draft ${draft.id.slice(0, 8)}… created for review.`);
      setDraftFormFor(null);
      setDraftTitle('');
      setDraftHypothesis('');
    } catch (err) {
      setPanelError(err instanceof Error ? err.message : 'Could not save the draft');
    }
  };

  const ctxSymbols = activeConversation?.context.instrument_symbols || [];
  const ctxDraftCount = ctxSelection.research_draft_ids.length;
  const ctxMemoryCount = ctxSelection.memory_ids.length;

  return (
    <div className="ws-ai-panel" data-testid="ai-panel">
      <div className="ws-ai-headrow">
        <span className="ws-ai-title">Personal AI assistant</span>
        {status && (
          <span className={`ws-badge ${status.mode === 'live' ? 'ws-badge-live' : ''}`} data-testid="ai-mode-badge">
            {status.mode === 'live' ? `AI live (${status.provider})` : 'AI simulated'}
          </span>
        )}
        <button className="ws-icon-btn" title="Test provider connection" onClick={testConnection} data-testid="ai-test-connection">
          Test
        </button>
      </div>
      {connResult && (
        <div className="ws-ai-conn" role="status">
          {connResult}
        </div>
      )}

      <div className="ws-ai-conv-row">
        <select
          className="ws-ai-select"
          value={activeId ?? ''}
          onChange={(e) => setActiveId(e.target.value || null)}
          aria-label="Choose an AI conversation"
          data-testid="ai-conversation-select"
        >
          {conversations.length === 0 && <option value="">(No conversations yet)</option>}
          {conversations.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button
          className="ws-icon-btn"
          title="New conversation"
          aria-label="New conversation"
          onClick={newConversation}
          data-testid="ai-new-conversation"
          style={{ whiteSpace: 'nowrap' }}
        >
          + New conversation
        </button>
        <button
          className="ws-icon-btn"
          title="Delete this conversation"
          aria-label="Delete this conversation"
          onClick={deleteConversation}
          disabled={!activeId}
        >
          ✕
        </button>
      </div>

      {conversations.length === 0 && (
        <div className="ws-muted" data-testid="ai-conversation-hint">
          No conversations yet: choose “+ New conversation” to start your first research
          conversation.
        </div>
      )}

      <div className="ws-ai-context">
        {ctxSymbols.map((s) => (
          <span key={s} className="ws-chip">{s}</span>
        ))}
        {ctxDraftCount > 0 && <span className="ws-chip">Drafts ×{ctxDraftCount}</span>}
        {ctxMemoryCount > 0 && <span className="ws-chip">Memory ×{ctxMemoryCount}</span>}
        {currentSymbol && (
          <button
            className="ws-icon-btn"
            title={`Add the current symbol ${currentSymbol} to the AI context`}
            aria-label={`Add the current symbol ${currentSymbol} to the AI context`}
            onClick={addCurrentSymbol}
            disabled={!activeId}
          >
            + {currentSymbol}
          </button>
        )}
        <button
          className="ws-icon-btn"
          title="Choose the research drafts and personal memory to give the AI"
          aria-label="Edit research context"
          onClick={openContextEditor}
          disabled={!activeId}
        >
          Context
        </button>
      </div>

      {ctxOpen && activeConversation && (
        <div className="ws-ai-ctx-editor" data-testid="ai-context-editor">
          <div className="ws-ai-ctx-section">Research drafts</div>
          {draftOptions.length === 0 && <div className="ws-muted">No drafts available</div>}
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
          <div className="ws-ai-ctx-section">Approved personal memory</div>
          {memoryOptions.length === 0 && <div className="ws-muted">No approved memory</div>}
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

      <div className="ws-ai-advisory">
        AI replies are for research reference only and do not authorize trading.
      </div>

      <div className="ws-ai-thread" ref={threadRef} data-testid="ai-thread">
        {messages.length === 0 && (
          <div className="ws-muted ws-ai-empty">
            Choose “+ New conversation” or pick an existing one; use “Context” to add research
            drafts, memory and instruments. (Current provider: {status?.provider ?? 'Unknown'})
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
                    setDraftTitle(`AI proposal: ${m.content.slice(0, 30)}`);
                    setDraftHypothesis(m.content);
                    setDraftSaved('');
                  }}
                  data-testid="ai-save-draft"
                >
                  Save as research draft
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
              placeholder="Draft title"
            />
            <textarea
              className="ws-input"
              value={draftHypothesis}
              onChange={(e) => setDraftHypothesis(e.target.value)}
              placeholder="Research hypothesis / AI proposal"
              rows={4}
              style={{ marginTop: 8, resize: 'vertical' }}
            />
            <div className="ws-ai-draft-form-actions">
              <button className="btn btn-primary" onClick={saveDraft} disabled={!draftTitle.trim()}>
                Create draft for review
              </button>
              <button className="ws-icon-btn" onClick={() => setDraftFormFor(null)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {draftSaved && (
          <div className="ws-ai-saved" role="status">
            {draftSaved} (awaiting human review)
          </div>
        )}
      </div>

      {panelError && (
        <div className="ws-message-error" role="alert">
          {panelError}
        </div>
      )}

      <div className="ws-ai-input-row">
        <textarea
          className="ws-ai-input"
          aria-label="Ask the AI assistant a research question"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask the AI assistant a research question…"
          rows={2}
          data-testid="ai-input"
        />
        <button className="btn btn-primary" onClick={send} disabled={sending || !activeId || !input.trim()} data-testid="ai-send">
          {sending ? 'Sending…' : 'Send'}
        </button>
      </div>
      {quotaUsed !== null && <div className="ws-muted ws-ai-quota">AI usage: {quotaUsed}</div>}
    </div>
  );
}
