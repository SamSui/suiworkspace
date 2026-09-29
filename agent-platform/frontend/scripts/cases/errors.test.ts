import { request, setToken } from '../../src/api/client'
import process from 'node:process'

setToken('t1')
globalThis.fetch = async () => ({
  ok: false, status: 404,
  headers: new Headers(),
  json: async () => ({ error: { code: 'not_found', message: 'x' } }),
}) as unknown as Response

try { await request('/v1/kb/1') } catch (e: any) {
  console.log('[B] mapped =', e.message)
  console.log('[B] code =', e.code)
  const ok = e.message === '资源不存在或无权访问' && e.code === 'not_found'
  console.log('[B-RESULT]', ok ? 'PASS' : 'FAIL')
}
process.exit(0)
