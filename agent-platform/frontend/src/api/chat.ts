/**
 * 对话接口：非流式 / SSE 流式（token/interrupt/done/error + 心跳）/ HITL resume。
 * 对齐 chat.py 已冻结 SSE 契约。
 */
import { request, streamPost } from './client'
import type { SseHandlers } from './client'
import type { ChatStartResponse } from './types'

export interface ChatStreamParams {
  query: string
  thread_id?: string | null
  conversation_id?: number | null
  kb_id?: number | null
}

/** RAG 引用标注来源信息（随后端引用事件扩展填充） */
export interface RagCitation {
  seq: number
  doc_id: number
  page?: number
  text?: string
  source?: string
}

/** POST /v1/chat：非流式对话，返回 message/thread id + usage */
export const apiChat = (params: ChatStreamParams): Promise<ChatStartResponse> =>
  request<ChatStartResponse>('/v1/chat', { method: 'POST', body: params })

/** POST /v1/chat/stream：SSE 流式对话（token/interrupt/done/error 事件逐条分发） */
export function apiChatStreamSse(
  params: ChatStreamParams,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamPost('/v1/chat/stream', params, handlers, signal)
}

/** POST /v1/chat/resume：HITL 恢复（以 thread_id 续跑挂起图） */
export function apiChatResume(
  thread_id: string,
  value: string,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamPost('/v1/chat/resume', { thread_id, value }, handlers, signal)
}

export type { SseHandlers }
