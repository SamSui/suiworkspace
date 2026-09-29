/**
 * 鉴权 / 用户接口（对齐后端 auth / users 路由，api-contract §2.1）
 */
import { request } from './client'
import type { TokenResponse, UserCreateResult, UserOut } from './types'

export interface LoginParams {
  name: string
  api_key: string
}

/** POST /v1/auth/token：用 name + api_key 换 JWT（公开） */
export const apiLogin = (params: LoginParams): Promise<TokenResponse> =>
  request<TokenResponse>('/v1/auth/token', {
    method: 'POST',
    body: params,
    skipAuth: true,
  })

/** POST /v1/auth/logout：无状态 204 占位；前端本地清 token */
export const apiLogout = async (): Promise<void> => {
  try {
    await request<void>('/v1/auth/logout', { method: 'POST', body: {} })
  } catch {
    /* 退出即使后端失败也要本地登出 */
  }
}

/** GET /v1/users/me：当前用户资料（鉴权闭环验证点） */
export const apiGetMe = (): Promise<UserOut> => request<UserOut>('/v1/users/me')

/** POST /v1/users：创建用户（公开；api_key 明文仅此一次） */
export const apiCreateUser = (name: string): Promise<UserCreateResult> =>
  request<UserCreateResult>('/v1/users', {
    method: 'POST',
    body: { name },
    skipAuth: true,
  })

/** POST /v1/users/me/api-key/rotate：轮换 api_key（新明文仅一次） */
export const apiRotateKey = (): Promise<UserCreateResult> =>
  request<UserCreateResult>('/v1/users/me/api-key/rotate', { method: 'POST', body: {} })
