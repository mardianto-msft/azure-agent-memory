import { useState, useRef, useEffect, useCallback } from 'react';
import { useMsal } from '@azure/msal-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './Chat.css';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  lastExtractedIndex: number;
  threadId: string | null;
}

const BACKEND_URL = (window as any).__BACKEND_URL__ || '';

function getBackendUrl(): string {
  if (BACKEND_URL) return BACKEND_URL;
  // Fallback: try to load from config.json (set at build/runtime)
  return '';
}

async function fetchBackendUrl(): Promise<string> {
  const cached = getBackendUrl();
  if (cached) return cached;
  try {
    const res = await fetch('/config.json');
    if (res.ok) {
      const cfg = await res.json();
      if (cfg.backendUrl) {
        (window as any).__BACKEND_URL__ = cfg.backendUrl;
        return cfg.backendUrl;
      }
    }
  } catch { /* ignore */ }
  return '';
}

export default function Chat() {
  const { instance, accounts } = useMsal();
  const account = accounts[0];
  const userId = (account?.idTokenClaims as Record<string, unknown>)?.oid as string | undefined;

  const initials = account?.name
    ?.split(' ')
    .map((n: string) => n[0])
    .join('')
    .toUpperCase() || '?';

  // State
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [backendUrl, setBackendUrl] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Load backend URL on mount
  useEffect(() => {
    fetchBackendUrl().then(setBackendUrl);
  }, []);

  const activeConversation = conversations.find(c => c.id === activeId) || null;
  const messages = activeConversation?.messages || [];

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px';
    }
  }, [input]);

  // ---------------------------------------------------------------------------
  // Memory extraction — incremental (sends only new messages since last extraction)
  // ---------------------------------------------------------------------------
  const EXTRACTION_INTERVAL = 8; // extract every 8 new messages (4 exchanges)
  const EXTRACTION_OVERLAP = 2;  // include 2 prior messages for context

  const storeMemories = useCallback(async (convId: string, wait = false) => {
    const conv = conversations.find(c => c.id === convId);
    if (!conv || conv.messages.length < 2) return;
    const url = backendUrl || await fetchBackendUrl();
    if (!url || !userId) return;

    const fromIndex = conv.lastExtractedIndex;
    if (fromIndex >= conv.messages.length) return; // nothing new

    const overlapStart = Math.max(0, fromIndex - EXTRACTION_OVERLAP);
    const slice = conv.messages.slice(overlapStart);
    if (slice.length < 2) return;

    try {
      console.log(`[storeMemories] Sending ${slice.length} messages for conversation ${convId} (wait=${wait})`);
      const resp = await fetch(`${url}/api/memories/store`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          conversation_id: convId,
          messages: slice.map(m => ({ role: m.role, content: m.content })),
          wait,
        }),
      });
      console.log(`[storeMemories] Response: ${resp.status}`);
      setConversations(prev =>
        prev.map(c =>
          c.id === convId ? { ...c, lastExtractedIndex: c.messages.length } : c
        )
      );
    } catch (err) {
      console.error('[storeMemories] Failed:', err);
    }
  }, [conversations, backendUrl, userId]);

  // Periodic incremental extraction — trigger when enough new messages accumulate
  useEffect(() => {
    if (!activeConversation || isStreaming) return;
    const newCount = activeConversation.messages.length - activeConversation.lastExtractedIndex;
    if (newCount >= EXTRACTION_INTERVAL) {
      storeMemories(activeConversation.id);
    }
  }, [activeConversation?.messages.length, isStreaming]);

  // Extract remaining memories on tab/browser close
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (!activeId || !userId) return;
      const conv = conversations.find(c => c.id === activeId);
      if (!conv || conv.messages.length <= conv.lastExtractedIndex) return;

      const overlapStart = Math.max(0, conv.lastExtractedIndex - EXTRACTION_OVERLAP);
      const slice = conv.messages.slice(overlapStart);
      if (slice.length < 2) return;

      const url = (window as any).__BACKEND_URL__ || '';
      if (!url) return;

      navigator.sendBeacon(
        `${url}/api/memories/store`,
        new Blob(
          [JSON.stringify({
            user_id: userId,
            conversation_id: activeId,
            messages: slice.map(m => ({ role: m.role, content: m.content })),
          })],
          { type: 'application/json' }
        )
      );
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [activeId, conversations, userId]);

  const createConversation = useCallback((): string => {
    const id = crypto.randomUUID();
    const conv: Conversation = { id, title: 'New chat', messages: [], lastExtractedIndex: 0, threadId: null };
    setConversations(prev => [conv, ...prev]);
    setActiveId(id);
    return id;
  }, []);

  const updateConversation = useCallback((id: string, updater: (c: Conversation) => Conversation) => {
    setConversations(prev => prev.map(c => c.id === id ? updater(c) : c));
  }, []);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    let convId = activeId;
    if (!convId) {
      convId = createConversation();
    }

    // Add user message
    const userMsg: ChatMessage = { role: 'user', content: text };
    updateConversation(convId, c => ({
      ...c,
      title: c.messages.length === 0 ? text.slice(0, 40) : c.title,
      messages: [...c.messages, userMsg],
    }));

    setInput('');
    setIsStreaming(true);

    // Add empty assistant message placeholder
    const assistantMsg: ChatMessage = { role: 'assistant', content: '' };
    updateConversation(convId, c => ({
      ...c,
      messages: [...c.messages, assistantMsg],
    }));

    try {
      const url = backendUrl || await fetchBackendUrl();
      // Get the thread ID for this conversation (may be null on first message)
      const currentConv = conversations.find(c => c.id === convId);
      const threadId = currentConv?.threadId || null;

      const response = await fetch(`${url}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          user_id: userId || undefined,
          thread_id: threadId,
        }),
      });

      if (!response.ok) throw new Error(`Backend error: ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let fullText = '';
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'content' && data.text) {
              fullText += data.text;
            } else if (data.type === 'done' && data.thread_id) {
              updateConversation(convId!, c => ({ ...c, threadId: data.thread_id }));
            } else if (data.type === 'error') {
              fullText += `\n\nError: ${data.message}`;
            }
          } catch { /* skip malformed JSON */ }
        }
      }

      // Simulate streaming: reveal text progressively
      if (fullText) {
        const CHUNK = 12;  // characters per tick
        for (let pos = CHUNK; pos < fullText.length; pos += CHUNK) {
          const partial = fullText.slice(0, pos);
          updateConversation(convId!, c => {
            const msgs = [...c.messages];
            msgs[msgs.length - 1] = { role: 'assistant', content: partial };
            return { ...c, messages: msgs };
          });
          await new Promise(r => setTimeout(r, 0));
        }
        // Final: show complete text
        updateConversation(convId!, c => {
          const msgs = [...c.messages];
          msgs[msgs.length - 1] = { role: 'assistant', content: fullText };
          return { ...c, messages: msgs };
        });
      }
    } catch (err: any) {
      updateConversation(convId!, c => {
        const msgs = [...c.messages];
        msgs[msgs.length - 1] = { role: 'assistant', content: `Sorry, something went wrong: ${err.message}` };
        return { ...c, messages: msgs };
      });
    } finally {
      setIsStreaming(false);
    }
  }, [input, isStreaming, activeId, backendUrl, conversations, createConversation, updateConversation]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleNewChat = () => {
    if (isStreaming) return;
    // Extract memories from the current conversation before switching
    if (activeId) storeMemories(activeId);
    createConversation();
    setInput('');
  };

  const handleSwitchConversation = (id: string) => {
    if (isStreaming || id === activeId) return;
    // Extract memories from the conversation we're leaving
    if (activeId) storeMemories(activeId);
    setActiveId(id);
  };

  const handleLogout = async () => {
    if (activeId) storeMemories(activeId);
    instance.logoutRedirect({ postLogoutRedirectUri: window.location.origin });
  };

  return (
    <div className="chat-layout">
      {/* ---- Sidebar ---- */}
      <aside className="chat-sidebar">
        <div className="sidebar-header">
          <h2>Agent Memory</h2>
        </div>

        <button className="btn-new-chat" onClick={handleNewChat}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>
          New chat
        </button>

        <div className="conversation-list">
          {conversations.map(c => (
            <div
              key={c.id}
              className={`conversation-item ${c.id === activeId ? 'active' : ''}`}
              onClick={() => handleSwitchConversation(c.id)}
            >
              {c.title}
            </div>
          ))}
        </div>

        <div className="sidebar-powered-by">
          Powered by
          <img src="/azure-logo.svg" alt="Azure" className="azure-logo" />
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-avatar">{initials}</div>
          <div className="sidebar-user">
            <div className="sidebar-user-name">{account?.name}</div>
            <div className="sidebar-user-email">{account?.username}</div>
          </div>
          <button className="btn-logout" onClick={handleLogout} title="Sign out">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
        </div>
      </aside>

      {/* ---- Main chat area ---- */}
      <main className="chat-main">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <div className="chat-empty-icon">💬</div>
            <h2>How can I help you today?</h2>
            <p>Send a message to start a conversation.</p>
          </div>
        ) : (
          <div className="chat-messages">
            <div className="chat-messages-inner">
              {messages.map((msg, i) => (
                <div key={i} className={`message-row ${msg.role}`}>
                  <div className={`message-avatar ${msg.role}`}>
                    {msg.role === 'assistant' ? '✦' : initials}
                  </div>
                  <div className="message-content">
                    {msg.role === 'assistant' && msg.content === '' && isStreaming ? (
                      <div className="typing-indicator">
                        <span /><span /><span />
                      </div>
                    ) : msg.role === 'assistant' ? (
                      <div className="markdown-body">
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={{
                            a: ({ children, href, ...props }) => (
                              <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                                {children}
                              </a>
                            ),
                          }}
                        >
                          {msg.content}
                        </ReactMarkdown>
                      </div>
                    ) : (
                      msg.content.split('\n').map((line, j) => (
                        <p key={j}>{line || '\u00A0'}</p>
                      ))
                    )}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          </div>
        )}

        {/* ---- Input ---- */}
        <div className="chat-input-area">
          <div className="chat-input-wrapper">
            <div className="chat-input-box">
              <textarea
                ref={textareaRef}
                rows={1}
                placeholder="Message Agent Memory..."
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isStreaming}
              />
              <button
                className="btn-send"
                onClick={handleSend}
                disabled={!input.trim() || isStreaming}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </button>
            </div>
            <div className="chat-disclaimer">
              Agent Memory may produce inaccurate information.
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
