/**
 * 文档 / 任务 / Agent 接口（document.py / task.py / agent.py）
 * 文档任务状态机：0 未处理 / 1 处理中 / 2 完成 / 3 失败。
 */
import { request } from './client'
import type { AgentConfigOut, DocumentOut, TaskOut } from './types'

/** POST /v1/doc：multipart 上传文档入队，202 返回 document(status=0) */
export async function apiUploadDoc(kb_id: number, file: File): Promise<DocumentOut> {
  const form = new FormData()
  form.append('kb_id', String(kb_id))
  form.append('file', file)
  return request<DocumentOut>('/v1/doc', { method: 'POST', body: form })
}

/** GET /v1/doc/{id}：文档元信息 */
export const apiGetDoc = (id: number): Promise<DocumentOut> =>
  request<DocumentOut>(`/v1/doc/${id}`)

/** DELETE /v1/doc/{id}：删除文档（204） */
export const apiDeleteDoc = (id: number): Promise<void> =>
  request<void>(`/v1/doc/${id}`, { method: 'DELETE' })

/** GET /v1/task/{id}：轮询摄入状态（status 与 document.status 严格一致） */
export const apiGetTask = (taskId: string): Promise<TaskOut> =>
  request<TaskOut>(`/v1/task/${taskId}`)

/** AGENT */
export const apiListAgents = (kb_id: number): Promise<AgentConfigOut[]> =>
  request<AgentConfigOut[]>(`/v1/agent?kb_id=${kb_id}`)

export interface CreateAgentParams {
  kb_id: number
  name: string
  graph_type: string
  temperature: number
  top_k: number
}
export const apiCreateAgent = (p: CreateAgentParams): Promise<AgentConfigOut> =>
  request<AgentConfigOut>('/v1/agent', { method: 'POST', body: p })

/** RAG 引用第 3 层原文（本期后端端点未落则 P2 占位） */
export const apiRagChunk = (doc_id: number, chunk_id: number): Promise<unknown> =>
  request<unknown>(`/v1/doc/${doc_id}/chunk/${chunk_id}`)
