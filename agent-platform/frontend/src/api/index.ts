/** API 统一出口：鉴权 / 知识库 / 文档任务 / 对话 */
export * from './types'
export { getToken, setToken, redirectToLogin, request } from './client'
export type { SseHandlers } from './client'
export * from './auth'
export * from './kb'
export * from './doc'
export * from './chat'