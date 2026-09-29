/**
 * 登录页（ADR §5.1）：name + api_key → POST /v1/auth/token。
 * 登录成功回跳 `redirect`（路由守卫写入），否则回工作台。
 * 也提供「读取/首次建号」入口（POST /v1/users 的 api_key 仅此一次）。
 */
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuthStore } from '../store/auth'
import { apiCreateUser } from '../api/auth'
import { Spinner } from '../components/ui/Spinner'
import { ErrorState } from '../components/ui/Feedback'

export default function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const redirect = params.get('redirect') || '/'

  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const doLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name || !apiKey) {
      setError('请输入用户名与访问令牌')
      return
    }
    setLoading(true)
    setError(null)
    try {
      await login(name.trim(), apiKey.trim())
      navigate(redirect, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请检查用户名与令牌')
    } finally {
      setLoading(false)
    }
  }

  // 空态：无账号 → 引导建号（api_key 明文仅此一次）
  const [haveMode, setRegisterMode] = useState(false)
  const [regKey, setRegKey] = useState<string | null>(null)

  const doRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name) {
      setError('请输入用户名')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await apiCreateUser(name.trim())
      setRegKey(res.api_key) // 明文仅此一次，请立即复制保存
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建用户失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md rounded-card bg-white p-8 shadow-card">
        <h1 className="text-xl font-semibold text-primary">智能体中台 · 统一入口</h1>
        <p className="mt-1 text-sm text-gray-500">
          登录即代表同意相关隐私与使用条款
        </p>

        {error && (
          <div className="mt-4">
            <ErrorState message={error} />
          </div>
        )}

        {regKey ? (
          <div className="mt-6 rounded-card bg-green-50 p-4">
            <p className="text-sm font-medium text-success">用户创建成功</p>
            <p className="mt-2 text-sm text-gray-700">
              访问令牌（明文仅此一次显示，请立即复制保存）：
            </p>
            <code className="mt-2 block break-all rounded bg-white p-2 text-xs">
              {regKey}
            </code>
            <button
              onClick={() => setRegKey(null)}
              className="mt-3 w-full rounded ctrl bg-primary px-4 py-2 text-sm text-white hover:bg-primary-hover"
            >
              我已保存，去登录
            </button>
          </div>
        ) : (
          <form onSubmit={haveMode ? doRegister : doLogin} className="mt-6 space-y-4">
            <div>
              <label className="mb-1 block text-sm text-gray-600">
                用户名 <span className="text-danger">*</span>
              </label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="你的用户名"
                className="w-full rounded ctrl border border-gray-300 px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />
            </div>
            {!haveMode ? (
              <div>
                <label className="mb-1 block text-sm text-gray-600">
                  访问令牌 <span className="text-danger">*</span>
                </label>
                <div className="flex gap-2">
                  <input
                    type={showKey ? 'text' : 'password'}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="api_key"
                    className="w-full rounded ctrl border border-gray-300 px-3 py-2 text-sm focus:border-primary focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey(!showKey)}
                    className="rounded ctrl border border-gray-300 px-3 text-xs text-gray-500 hover:bg-gray-50"
                  >
                    {showKey ? '隐藏' : '显示'}
                  </button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-gray-400">创建后会用刚出现的访问令牌登录</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded ctrl bg-primary px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-60"
            >
              {loading && <Spinner size="sm" className="border-white" />}
              {haveMode ? '创建用户' : '登录'}
            </button>

            <button
              type="button"
              onClick={() => {
                setRegisterMode(!haveMode)
                setError(null)
              }}
              className="w-full text-center text-xs text-primary hover:underline"
            >
              {haveMode ? '已有账号？去登录' : '暂无账号？点击建号（获取访问令牌）'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
