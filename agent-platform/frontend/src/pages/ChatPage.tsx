/**
 * 对话工作台（ADR §5.4 / 决策2）—— 核心验收页 1。
 * 三栏可折叠：左会话列表 / 中消息流（SSE 增量渲染）/ 右 RAG 引用 + Agent 元数据。
 * 发送 → POST /v1/chat/stream（token 逐 token 追加）→ interrupt 转 HITL 审批条 → resume。
 */
import { useState } from 'react'
import { useChatStore } from '../store/chat'
import type { ChatMessage } from '../store/chat'

export default function ChatPage() {
  const {
    threads,
    activeThreadId,
    streaming,
    newThread,
    setActive,
    sendMessage,
    stopStreaming,
  } = useChatStore()
  const [input, setInput] = useState('')
  const [rightCollapsed, setRightCollapsed] = useState(false)

  const active = threads.find((t) => t.id === activeThreadId) ?? null

  const doSend = (e: React.FormEvent) => {
    e.preventDefault()
    const q = input.trim()
    if (!q || streaming) return
    setInput('')
    void sendMessage(q)
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      {/* 左：会话列表 */}
      <aside className="w-56 shrink-0 overflow-y-auto rounded-card bg-white p-3 shadow-card">
        <button
          onClick={() => newThread()}
          className="mb-2 w-full rounded ctrl bg-primary px-3 py-2 text-sm text-white hover:bg-primary-hover"
        >
          ＋ 新会话
        </button>
        <ul className="space-y-1">
          {threads.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => setActive(t.id)}
                className={`w-full truncate rounded px-3 py-2 text-left text-sm ${
                  t.id === activeThreadId
                    ? 'bg-primary-light text-primary'
                    : 'text-gray-700 hover:bg-gray-50'
                }`}
              >
                {t.title || '新对话'}
              </button>
            </li>
          ))}
          {threads.length === 0 && (
            <li className="px-3 py-2 text-sm text-gray-400">暂无会话</li>
          )}
        </ul>
      </aside>

      {/* 中：消息流 */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-card bg-white shadow-card">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {active && active.messages.length === 0 && (
            <p className="pt-10 text-center text-sm text-gray-400">
              输入问题（可绑定知识库做 RAG），回车发送
            </p>
          )}
          {active?.messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}
        </div>

        {/* 输入区 + 停止/发送 */}
        <form
          onSubmit={doSend}
          className="flex items-end gap-2 border-t border-gray-100 p-3"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                doSend(e)
              }
            }}
            placeholder="输入问题，Enter 发送 / Shift+Enter 换行…"
            rows={2}
            className="flex-1 resize-none rounded ctrl border border-gray-300 p-3 text-sm focus:border-primary focus:outline-none"
          />
          {streaming ? (
            <button
              type="button"
              onClick={stopStreaming}
              className="rounded ctrl bg-danger px-4 py-2 text-sm text-white hover:opacity-90"
            >
              停止
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="rounded ctrl bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover disabled:opacity-60"
            >
              发送
            </button>
          )}
        </form>
      </main>

      {/* 右：RAG 引用 + 元数据（可折叠；ADR §5.4） */}
      {rightCollapsed ? (
        <button
          onClick={() => setRightCollapsed(false)}
          className="h-fit self-center rounded ctrl border border-gray-300 bg-white px-2 py-2 text-xs text-gray-500"
        >
          《
        </button>
      ) : (
        <aside className="flex w-72 shrink-0 flex-col gap-3 rounded-card bg-white p-4 shadow-card">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">本次对话</h3>
            <button
              onClick={() => setRightCollapsed(true)}
              className="text-xs text-gray-400 hover:text-primary"
            >
              折叠 »
            </button>
          </div>
          <dl className="space-y-1 text-sm text-gray-600">
            <div className="flex justify-between">
              <dt className="text-gray-400">Agent</dt>
              <dd>默认</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-400">知识库</dt>
              <dd>未绑定</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-400">trace_id</dt>
              <dd className="font-mono text-xs">—</dd>
            </div>
          </dl>
          <div className="flex-1 overflow-y-auto">
            <h4 className="mb-1 text-xs font-medium text-gray-400">RAG 引用</h4>
            {active && active.messages.some((m) => m.citations.length > 0) ? (
              active.messages
                .flatMap((m) => m.citations)
                .map((c, i) => (
                  <div
                    key={i}
                    className="mb-2 rounded ctrl border border-gray-100 px-2 py-1.5 text-xs text-gray-600"
                  >
                    #{c.seq} · doc:{c.doc_id}
                    <br />
                    <span className="text-gray-300">来源卡片（[N] 点击回原文/P2）</span>
                  </div>
                ))
            ) : (
              <p className="text-xs text-gray-300">暂无引用</p>
            )}
          </div>
        </aside>
      )}
    </div>
  )
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[75%] rounded-card px-4 py-2 text-sm ${
          isUser ? 'bg-primary text-white' : 'border border-gray-100 bg-gray-50'
        }`}
      >
        <p className="whitespace-pre-wrap break-words">{msg.content}</p>

        {msg.status === 'waiting_hitl' && <HitlBar />}

        {msg.status === 'error' && msg.error && (
          <p className="mt-1 text-xs text-danger">{msg.error}</p>
        )}
        {msg.status === 'streaming' && (
          <p className="mt-1 text-xs text-blue-600">● 生成中</p>
        )}
        {msg.status === 'stopped' && (
          <p className="mt-1 text-xs text-gray-400">已停止</p>
        )}
        {msg.status === 'done' && !isUser && (
          <p className="mt-1 text-xs text-gray-300">已生成</p>
        )}
      </div>
    </div>
  )
}

/** HITL 审批条（ADR §5.4 / 决策2）：interrupt 后在此「通过 / 拒绝」→ POST /v1/chat/resume */
function HitlBar() {
  const resolveHitl = useChatStore((s) => s.resolveHitl)
  const activeThreadId = useChatStore((s) => s.activeThreadId)

  return (
    <div className="mt-2 flex items-center gap-2 border-t border-dashed border-gray-200 pt-2">
      <span className="text-xs text-accent">待人工确认</span>
      <button
        onClick={() => activeThreadId && void resolveHitl(activeThreadId, 'accept')}
        className="rounded ctrl bg-success px-2 py-1 text-xs text-white"
      >
        通过
      </button>
      <button
        onClick={() => activeThreadId && void resolveHitl(activeThreadId, 'reject')}
        className="rounded ctrl border border-gray-300 px-2 py-1 text-xs text-gray-600"
      >
        拒绝
      </button>
    </div>
  )
}