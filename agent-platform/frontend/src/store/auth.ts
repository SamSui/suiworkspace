/**
 * 鉴权状态（Zustand）：token + 用户信息 + 登录/登出/续拉起。
 * 路由守卫依据 token 判放行；失效由 401 拦截 / fetchMe 兜底清态。
 */
import { create } from 'zustand'
import { apiGetMe, apiLogin, apiLogout } from '../api/auth'
import { getToken, setToken } from '../api/client'
import type { UserOut } from '../api/types'

interface AuthState {
  user: UserOut | null
  token: string | null
  ready: boolean
  /** 登录：换 token → 拉 /users/me 闭环校验 */
  login: (name: string, apiKey: string) => Promise<UserOut>
  /** 启动恢复：token 有效则拉用户，否则清态 */
  fetchMe: () => Promise<UserOut | null>
  logout: () => Promise<void>
  clear: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: getToken(),
  ready: false,

  login: async (name, apiKey) => {
    const res = await apiLogin({ name, api_key: apiKey })
    setToken(res.access_token)
    const me = await apiGetMe()
    set({ user: me, token: res.access_token, ready: true })
    return me
  },

  fetchMe: async () => {
    if (!getToken()) {
      set({ user: null, token: null, ready: true })
      return null
    }
    try {
      const me = await apiGetMe()
      set({ user: me, ready: true })
      return me
    } catch {
      setToken(null)
      set({ user: null, token: null, ready: true })
      return null
    }
  },

  logout: async () => {
    try {
      await apiLogout()
    } finally {
      setToken(null)
      set({ user: null, token: null, ready: true })
    }
  },

  clear: () => {
    setToken(null)
    set({ user: null, token: null, ready: true })
  },
}))

/** 是否已登录（有 token 即视为登录态；失效由 401 拦截兜底） */
export const isAuthenticated = (): boolean => !!getToken()
