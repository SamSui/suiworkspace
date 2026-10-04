/**
 * 基础请求封装（步骤 2 核心交付）：
 *  - fetch 统一封装，自动注入 `Authorization: Bearer <token>`
 *  - 401 / `unauthenticated` → 清 token + 统一跳登录（带 redirect 回跳）
 *  - 后端错误体 `{error:{code,message}}` 解析 → 映射为可读文案（ERROR_MESSAGES）
 */
import type { ErrorBody } from './types'
import { ApiError, ERROR_MESSAGES } from './types'

// ---------- token 存取（内存优先；可选用 localStorage 持久化） ----------

const TOKEN_KEY = 'agent_platform_token'
const storage =
  import.meta.env.VITE_TOKEN_STORAGE === 'localStorage' ? localStorage : null

let _token: string | null = null
if (storage) {
  try {
    _token = storage.getItem(TOKEN_KEY)
  } catch {
    _token = null
  }
}

/** 读取当前访问令牌 */
export function getToken(): string | null {
  return _token
}

/** 设置 / 清除访问令牌（内存 + 可选持久化） */
export function setToken(token: string | null): void {
  _token = token
  if (storage) {
    try {
      if (token) storage.setItem(TOKEN_KEY, token)
      else storage.removeItem(TOKEN_KEY)
    } catch {
      /* 持久化失败不阻断使用 */
    }
  }
}

/** 记录登录后要回跳的原路径，并跳转登录页 */
export function redirectToLogin(current = window.location.pathname + window.location.search): void {
  if (window.location.pathname.startsWith('/login')) return
  window.location.href = `/login?redirect=${encodeURIComponent(current)}`
}

function onUnauthenticated(): void {
  setToken(null)
  redirectToLogin()
}

// ---------- fetch 封装 ----------

interface RequestOptions extends Omit<RequestInit, 'body'> {
  /** 允许不带 token 的公开请求（如 POST /v1/auth/token） */
  skipAuth?: boolean
  /** 关闭通用 401 跳登录（默认开启） */
  handleAuth?: boolean
  body?: BodyInit | object | null
}

const DEFAULT_TIMEOUT_MS = 30_000

async function parseError(res: Response): Promise<ApiError> {
  let code = 'unknown'
  let message = `请求失败（HTTP ${res.status}）`
  let detail: unknown
  try {
    const data = (await res.json()) as ErrorBody
    if (data?.error) {
      code = data.error.code
      message = data.error.message || ''
      detail = data.error.detail
    }
  } catch {
    /* 非 JSON 响应体，仅用默认文案 */
  }
  const readable = ERROR_MESSAGES[code] || message
  return new ApiError(readable, code, res.status, detail)
}

/** 统一请求入口（JSON in / JSON out） */
export async function request<T>(
  url: string,
  options: RequestOptions = {},
): Promise<T> {
  const { skipAuth = false, handleAuth = true, ...init } = options
  const token = getToken()

  const headers = new Headers(init.headers)
  if (token && !skipAuth) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  let body: BodyInit | null | undefined
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData
  if (options.body != null && typeof options.body === 'object' && !isForm) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(options.body as object)
  } else {
    body = (options.body as BodyInit | null | undefined) ?? undefined
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)
  try {
    const res = await fetch(url, {
      ...init,
      headers,
      body,
      signal: controller.signal,
    })
    if (res.status === 401 && handleAuth) {
      const err = await parseError(res)
      onUnauthenticated()
      throw err
    }
    if (!res.ok) {
      throw await parseError(res)
    }
    if (res.status === 204) {
      return undefined as T
    }
    return (await res.json()) as T
  } catch (e) {
    if (e instanceof ApiError) throw e
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError('请求超时，请稍后再试', 'timeout', 0)
    }
    throw new ApiError('网络异常，请检查后端服务是否可用', 'network', 0)
  } finally {
    clearTimeout(timer)
  }
}

// ---------- SSE 客户端（POST 流式对话，逐事件透传） ----------

export type SseHandlers = {
  onToken: (text: string, meta: Record<string, unknown>) => void
  onInterrupt?: (payload: unknown) => void
  onDone?: (payload: unknown) => void
  onError: (code: string, trace_id: string, message?: string) => void
}

/**
 * 以 `fetch` + `ReadableStream` 消费 SSE 事件流，按 event 分发（token/interrupt/done/error）。
 * heartbeat 注释行（`:ping`）忽略。
 */
export async function streamPost(
  url: string,
  body: object,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  const controller = new AbortController()
  const onAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onAbort)
  }
  const timer = setTimeout(() => controller.abort(), 5 * 60_000)

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (res.status === 401) {
      onUnauthenticated()
      handlers.onError('unauthenticated', '', '登录已过期，请重新登录')
      return
    }
    if (!res.ok || !res.body) {
      handlers.onError('http_error', '', `对话请求失败（HTTP ${res.status}）`)
      return
    }

    // 增量解析 SSE：按事件块（以空行分隔）逐条处理，不重建 DOM（ADR 决策2）
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let event = 'message'
    let dataLines: string[] = []

    const dispatchEvent = () => {
      const hasData = dataLines.length > 0
      const dataStr = dataLines.join('\n')
      dataLines = []
      if (!hasData) return
      let payload: unknown
      try {
        payload = dataStr ? JSON.parse(dataStr) : null
      } catch {
        payload = dataStr
      }
      switch (event) {
        case 'token': {
          // 契约收敛：后端 `sse.py` 的 token 事件 data 形为 `{"seq":N,"text":"..."}`，
          // 字段是 `text`（非 `token`）。此处 `text` 优先，`token` 兼容旧桩/旧实现。
          const p = payload as { text?: string; token?: string } | null | string
          const text =
            (p && typeof p === 'object' && typeof p.text === 'string' && p.text) ||
            (p && typeof p === 'object' && typeof p.token === 'string' && p.token) ||
            String(payload ?? '')
          handlers.onToken(text, (payload as Record<string, unknown>) ?? {})
          break
        }
        case 'interrupt':
          handlers.onInterrupt?.(payload)
          break
        case 'done':
          handlers.onDone?.(payload)
          break
        case 'error':
          {
            const e = (payload as { code?: string; trace_id?: string; message?: string }) ?? {}
            handlers.onError(e.code ?? 'error', e.trace_id ?? '', e.message ?? '对话出错')
          }
          break
        default:
          break
      }
    }

    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 按双换行（SSE 块分隔）切分；每解析完一个块立即分发，保证逐事件增量
      let sepIdx: number
      while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + 2)
        for (const line of block.split('\n')) {
          if (line.startsWith(':')) continue // 心跳 / 注释行
          if (line.startsWith('event:')) {
            event = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trimStart())
          }
        }
        dispatchEvent()
      }
    }
    // 流尾残留的最后一个未封口块
    dispatchEvent()
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}
