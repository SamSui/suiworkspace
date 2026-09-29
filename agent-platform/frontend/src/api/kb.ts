/**
 * 知识库接口（GET/POST/PATCH/DELETE /v1/kb，对齐 knowledge.py）
 */
import { request } from './client'
import type { KnowledgeBaseOut } from './types'

/** GET /v1/kb：列出当前用户的库 */
export const apiListKb = (): Promise<KnowledgeBaseOut[]> => request('/v1/kb')

/** POST /v1/kb：创建知识库 */
export const apiCreateKb = (name: string): Promise<KnowledgeBaseOut> =>
  request('/v1/kb', { method: 'POST', body: { name } })

/** PATCH /v1/kb/{id}：重命名（软更名） */
export const apiUpdateKb = (id: number, name: string): Promise<KnowledgeBaseOut> =>
  request(`/v1/kb/${id}`, { method: 'PATCH', body: { name } })

/** DELETE /v1/kb/{id}：软删（status=0） */
export const apiDeleteKb = (id: number): Promise<void> =>
  request<void>(`/v1/kb/${id}`, { method: 'DELETE' })
