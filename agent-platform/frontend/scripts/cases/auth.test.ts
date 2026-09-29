import { request, setToken, getToken } from '../../src/api/client'
import process from 'node:process'

globalThis.window = { location: { pathname: '/kb', search: '', href: '' } }
setToken('t1')
globalThis.fetch = async () => ({
  ok: false, status: 401,
  headers: new Headers(),
  json: async () => ({ error: { code: 'unauthenticated', message: 'expired' } }),
}) as unknown as Response
let thrown: any = null
try { await request('/v1/kb') } catch (e) { thrown = e }
const href = (globalThis.window as any).location.href
console.log('[C] token-cleared =', getToken() === null)
console.log('[C] redirect-href =', href)
console.log('[C] thrown-code =', (thrown && thrown.code) || null)
const ok = getToken() === null && String(href).startsWith('/login?redirect=')
console.log('[C-RESULT]', ok ? 'PASS' : 'FAIL')
process.exit(ok ? 0 : 1)
