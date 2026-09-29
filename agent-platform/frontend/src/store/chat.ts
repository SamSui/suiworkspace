/**
 * 对话工作台状态：会话列表 + 消息流 + SSE 增量追加 + HITL 审批态 + 停止生成。
 *
 * SSE `token` 事件按文本追加到当前 assistant 消息（仅更新字段，不重建父节点，
 * —— ADR 决策2），保证长回答流式渲染不卡顿。
 * 本地用 `id` 作消息/线程寻址键；服务端 thread_id 在已接收流后回填 `thread_id_server`。
 */
import { create } from 'zustand'
import { apiChatResume, apiChatStreamSse } from '../api/chat'
import type { ChatStreamParams, RagCitation } from '../api/chat'

export type MessageRole = 'user' | 'assistant'
export type MessageStatus =
  | 'streaming'
  | 'done'
  | 'error'
  | 'waiting_hitl'
  | 'stopped'

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  status: MessageStatus
  citations: RagCitation[]
  trace_id?: string
  error?: string
}

export interface Thread {
  id: string // 本地键
  thread_id?: string | null // 服务端 langgraph thread_id
  title: string
  kb_id?: number | null
  messages: ChatMessage[]
}

interface ChatState {
  threads: Thread[]
  activeThreadId: string | null
  streaming: boolean
  activeAbort?: AbortController

  newThread: (kb_id?: number | null) => string
  setActive: (id: string) => void
  getActive: () => Thread | null
  sendMessage: (query: string, opts?: { kb_id?: number | null }) => Promise<void>
  stopStreaming: () => void
  resolveHitl: (threadId: string, value: string) => Promise<void>
}

let counter = 0
const uid = (p: string) => `${p}-${Date.now().toString(36)}-${(++counter).toString(36)}`

function patchMessage(
  threads: Thread[],
  threadId: string,
  msgId: string,
  patch: Partial<ChatMessage>,
): Thread[] {
  return threads.map((t) =>
    t.id === threadId
      ? { ...t, messages: t.messages.map((m) => (m.id === msgId ? { ...m, ...patch } : m)) }
      : t,
  )
}

export const useChatStore = create<ChatState>((set, get) => ({
  threads: [],
  activeThreadId: null,
  streaming: false,

  newThread: (kb_id) => {
    const id = uid('thread')
    set((s) => ({
      threads: [...s.threads, { id, title: '新对话', kb_id: kb_id ?? null, messages: [] }],
      activeThreadId: id,
    }))
    return id
  },

  setActive: (id) => set({ activeThreadId: id }),

  getActive: () => {
    const { threads, activeThreadId } = get()
    return threads.find((t) => t.id === activeThreadId) ?? null
  },

  sendMessage: async (content, opts = {}) => {
    const state = get()
    if (state.streaming) return
    const tid = state.activeThreadId ?? state.newThread(opts.kb_id)
    const thread = state.threads.find((t) => t.id === tid)
    if (!thread) return

    const userMsg: ChatMessage = {
      id: uid('u'),
      role: 'user',
      content,
      status: 'done',
      citations: [],
    }
    const aiMsg: ChatMessage = {
      id: uid('a'),
      role: 'assistant',
      content: '',
      status: 'streaming',
      citations: [],
    }
    const abort = new AbortController()
    const cur = get().threads.find((t) => t.id === tid)!
    set({
      streaming: true,
      activeAbort: abort,
      threads: get().threads.map((t) =>
        t.id === tid
          ? {
              ...t,
              title: t.title === '新对话' ? content.slice(0, 16) : t.title,
              kb_id: opts.kb_id ?? t.kb_id,
              messages: [...t.messages, userMsg, aiMsg],
            }
          : t,
      ),
    })

    const params: ChatStreamParams = {
      query: content,
      thread_id: cur.thread_id ?? null,
      conversation_id: null,
      kb_id: opts.kb_id ?? cur.kb_id ?? null,
    }

    try {
      await apiChatStreamSse(
        params,
        {
          onToken: (text) => {
            set((s) => ({
              threads: patchMessage(s.threads, tid, aiMsg.id, {
                content:
                  (s.threads.find((t) => t.id === tid)?.messages.find((m) => m.id === aiMsg.id)?.content ??
                    '') + text,
              }),
            }))
          },
          onInterrupt: () => {
            set((s) => ({
              threads: patchMessage(s.threads, tid, aiMsg.id, { status: 'waiting_hitl' }),
            }))
          },
          onDone: (payload) => {
            const p = (payload as { thread_id?: string } | null) ?? {}
            set((s) => ({
              streaming: false,
              activeAbort: undefined,
              threads: s.threads.map((t) =>
                t.id === tid
                  ? {
                      ...t,
                      thread_id: p.thread_id ?? t.thread_id,
                      messages: t.messages.map((m) =>
                        m.id === aiMsg.id ? { ...m, status: 'done' } : m,
                      ),
                    }
                  : t,
              ),
            }))
          },
          onError: (_code, _trace, msg) => {
            set((s) => ({
              streaming: false,
              activeAbort: undefined,
              threads: patchMessage(s.threads, tid, aiMsg.id, {
                status: 'error',
                error: msg || '对话出错',
              }),
            }))
          },
        },
        abort.signal,
      )
    } catch (e) {
      // 手动 stop 或异常
      const stopped = abort.signal.aborted
      set((s) => ({
        streaming: false,
        activeAbort: undefined,
        threads: patchMessage(s.threads, tid, aiMsg.id, {
          status: stopped ? 'stopped' : 'error',
          error: stopped ? '已停止生成' : '对话出错',
        }),
      }))
      void e
    }
  },

  stopStreaming: () => {
    const st = get()
    st.activeAbort?.abort()
    const active = st.getActive()
    const ai = active?.messages.slice(-1)[0]
    if (ai?.status === 'streaming') {
      set((s) => ({
        streaming: false,
        activeAbort: undefined,
        threads: patchMessage(s.threads, st.activeThreadId ?? '', ai.id, {
          status: 'stopped',
        }),
      }))
    }
  },

  resolveHitl: async (threadId, value) => {
    const thread = get().threads.find((t) => t.id === threadId)
    if (!thread?.thread_id) return
    const aid = thread.messages.find((m) => m.role === 'assistant' && m.status === 'waiting_hitl')?.id
    if (!aid) return
    const abort = new AbortController()
    await apiChatResume(
      thread.thread_id,
      value,
      {
        onToken: (text) => {
          set((s) => ({
            threads: patchMessage(s.threads, threadId, aid, {
              content:
                  (s.threads.find((t) => t.id === threadId)?.messages.find((m) => m.id === aid)?.content ??
                    '') + text,
            }),
          }))
        },
        onInterrupt: () => {},
        onDone: () => {
          set((s) => ({
            threads: patchMessage(s.threads, threadId, aid, { status: 'done' }),
          }))
        },
        onError: (_c, _m, msg) => {
          set((s) => ({
            threads: patchMessage(s.threads, threadId, aid, { status: 'error', error: msg || '恢复失败' }),
          }))
        },
      },
      abort.signal,
    )
  },
}))
