/**
 * 后端统一错误体与鉴权 token。
 * 对齐 api-contract.md：所有错误 `{"error": {code, message, detail?}}`。
 */

export interface ErrorBody {
  error: {
    code: string // unauthenticated | not_found | invalid_argument | rate_limited | ...
    message: string
    detail?: unknown
  }
}

/** 错误码 → 用户可读文案（ADR §决策4 对齐契约） */
export const ERROR_MESSAGES: Record<string, string> = {
  unauthenticated: '登录已过期，请重新登录',
  not_found: '资源不存在或无权访问',
  invalid_argument: '请求参数不合法',
  rate_limited: '请求太频繁，请稍后再试',
}

/** 后端 API 业务错误（携带 code / HTTP 状态） */
export class ApiError extends Error {
  code: string
  status: number
  detail?: unknown

  constructor(message: string, code = 'unknown', status = 0, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.detail = detail
  }
}

// ---------- 各端点的返回类型（对齐后端 schemas） ----------

/** 用户视图（从不含 api_key） */
export interface UserOut {
  id: number
  name: string
  status: number
  created_at: string
}

/** POST /v1/auth/token 响应 */
export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: UserOut
}

/** POST /v1/users 创建用户（api_key 明文仅此一次） */
export interface UserCreateResult {
  user: UserOut
  api_key: string
}

/** 知识库 KnowledgeBaseOut */
export interface KnowledgeBaseOut {
  id: number
  name: string
  owner_id: number
  status: number
}

/** 文档 DocumentOut（image entry 行内用） */
export interface DocumentOut {
  id: number
  kb_id: number
  file_name: string
  status: number // 0 未处理 1 处理中 2 完成 3 失败
  chunk_count: number
}

/** 任务 TaskOut（task_id == document.id，status 同构） */
export interface TaskOut {
  task_id: string
  doc_id: number
  status: number
  progress: number
  error_msg: string | null
}

/** Agent 配置 AgentConfigOut */
export interface AgentConfigOut {
  id: number
  kb_id: number
  name: string
  graph_type: string
  temperature: number
  top_k: number
  conf: Record<string, unknown>
  version: number
  status: number
}

/** 非流式对话响应 ChatStartResponse */
export interface ChatStartResponse {
  thread_id: string
  conversation_id: number | null
  message_id: number
  usage: Record<string, number>
}

/** RAG 引用第 3 层原文（GET /v1/doc/{id}/chunk/{chunk_id}；未落则前端 P2 占位） */
export interface RagChunkOut {
  chunk_id: number
  doc_id: number
  kb_id: number
  text: string
}
