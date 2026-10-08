import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';

export default function ChatPanel({ estateId }) {
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const listRef = useRef(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, loading]);

  const ask = async (text) => {
    const q = (text ?? question).trim();
    if (!q) return;
    setLoading(true);
    setQuestion('');
    setMessages((m) => [...m, { role: 'user', content: q }]);
    try {
      const res = await axios.post(`/api/estates/${estateId}/chat`, { question: q, session_id: sessionId });
      setSessionId(res.data.session_id);
      setMessages((m) => [...m, { role: 'assistant', content: res.data.answer, citations: res.data.citations, was_refused: res.data.was_refused }]);
    } catch (e) {
      setMessages((m) => [...m, { role: 'assistant', content: 'Error: ' + (e.response?.data?.detail || e.message), isError: true }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[640px] bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800 bg-gradient-to-r from-indigo-50 via-white to-violet-50 dark:from-gray-900 dark:via-gray-900 dark:to-gray-900 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-xl bg-indigo-600 flex items-center justify-center text-white shadow-sm">✦</div>
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-white">Estate Chat</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Grounded in the knowledge graph · cites FQNs</div>
          </div>
        </div>
        <div className="hidden sm:flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
          <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          grounded · no hallucination
        </div>
      </div>

      {/* Messages */}
      <div ref={listRef} className="flex-1 overflow-y-auto px-4 sm:px-6 py-6 space-y-4 bg-gray-50/60 dark:bg-gray-950/40">
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center py-12">
            <div className="h-14 w-14 rounded-2xl bg-indigo-600/10 dark:bg-indigo-500/10 flex items-center justify-center mb-4 text-indigo-600 dark:text-indigo-400 text-xl">💬</div>
            <div className="text-sm font-medium text-gray-900 dark:text-white">Ask me anything about the estate</div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-1 max-w-sm">Try lineage, impact, orphans, or hot tables.</div>
          </div>
        )}

        {messages.map((m, i) => {
          const isUser = m.role === 'user';
          return (
            <div key={i} className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}>
              {!isUser && (
                <div className={`h-8 w-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${m.isError ? 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-300' : m.was_refused ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300' : 'bg-indigo-600 text-white'}`}>
                  {m.isError ? '!' : m.was_refused ? '?' : '✦'}
                </div>
              )}
              <div className={`max-w-[78%] rounded-2xl px-4 py-3 shadow-sm text-sm leading-relaxed ${isUser ? 'bg-indigo-600 text-white rounded-br-sm' : m.isError ? 'bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 text-red-700 dark:text-red-300 rounded-bl-sm' : m.was_refused ? 'bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 text-amber-800 dark:text-amber-200 rounded-bl-sm' : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-100 rounded-bl-sm'}`}>
                <div className={`prose prose-sm max-w-none ${isUser ? 'prose-invert' : 'dark:prose-invert'} prose-p:my-1 prose-ul:my-1 prose-li:my-0`}>
                  <ReactMarkdown>{m.content}</ReactMarkdown>
                </div>
                {m.was_refused && !m.isError && <div className="mt-1 text-xs opacity-80">Refused - no grounding in the graph</div>}
              </div>
              {isUser && <div className="h-8 w-8 rounded-full bg-gray-900 dark:bg-white text-white dark:text-gray-900 flex items-center justify-center text-xs font-medium shrink-0">You</div>}
            </div>
          );
        })}

        {loading && (
          <div className="flex gap-3 justify-start">
            <div className="h-8 w-8 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs">✦</div>
            <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
              <div className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-gray-300 dark:bg-gray-600 animate-bounce [animation-delay:-0.3s]" />
                <span className="h-2 w-2 rounded-full bg-gray-300 dark:bg-gray-600 animate-bounce [animation-delay:-0.15s]" />
                <span className="h-2 w-2 rounded-full bg-gray-300 dark:bg-gray-600 animate-bounce" />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="px-4 sm:px-6 py-4 border-t border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 space-y-3">
        <div className="flex items-end gap-2 sm:gap-3">
          <div className="flex-1 relative">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}
              placeholder="Ask me anything about the estate"
              className="w-full pl-4 pr-12 py-3 rounded-2xl bg-gray-100 dark:bg-gray-800 border border-transparent focus:bg-white dark:focus:bg-gray-900 focus:border-indigo-300 dark:focus:border-indigo-700 focus:ring-4 focus:ring-indigo-100 dark:focus:ring-indigo-900/30 outline-none text-sm text-gray-900 dark:text-white placeholder:text-gray-400 transition"
            />
            <div className="absolute right-1.5 top-1.5 bottom-1.5 hidden sm:flex items-center text-[11px] text-gray-400 pr-2">↵</div>
          </div>
          <button
            onClick={() => ask()}
            disabled={loading || !question.trim()}
            className="h-[44px] w-[44px] sm:h-[46px] sm:w-auto sm:px-5 inline-flex items-center justify-center rounded-2xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed text-white shadow-sm transition"
            aria-label="Send"
          >
            <span className="hidden sm:inline text-sm font-medium">Send</span>
            <span className="sm:hidden">↑</span>
          </button>
        </div>
        <div className="text-[11px] text-center text-gray-400 dark:text-gray-500">Grounded answers only · cites FQNs · refuses when not in graph</div>
      </div>
    </div>
  );
}
