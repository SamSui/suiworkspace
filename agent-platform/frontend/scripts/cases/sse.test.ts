import { streamPost, setToken } from '../../src/api/client'
import process from 'node:process'

function sse(chunks: string[]) {
  const encoder = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(c) {
      if (i >= chunks.length) { c.close(); return }
      c.enqueue(encoder.encode(chunks[i++]))
    },
  })
}
const ev = (e: string, d: string) => 'event: ' + e + '\ndata: ' + d + '\n\n'
const parts = [
  ev('token', '{"token":"你"}'),
  ev('token', '{"token":"好"}'),
  ev('interrupt', '{"reason":"需要确认"}'),
  ev('done', '{"message_id":1,"thread_id":"abc"}'),
  ev('error', '{"code":"boom","trace_id":"T1"}'),
]
// 故意把事件切到跨 chunk 边界，验证增量解析不重建父节点
const chunks = [
  parts[0].slice(0, 9), parts[0].slice(9),
  parts[1] + parts[2].slice(0, 5),
  parts[2].slice(5), parts[3], parts[4],
]

globalThis.fetch = async () =>
  new Response(sse(chunks), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })

setToken('t1')
const tokens: string[] = []
let interrupt: unknown = null
let done: unknown = null
let err: unknown = null
await streamPost('/v1/chat/stream', { query: 'q' }, {
  onToken: (t) => tokens.push(t),
  onInterrupt: (p) => { interrupt = p },
  onDone: (p) => { done = p },
  onError: (c, tid) => { err = { c, tid } },
})

const joined = tokens.join('')
console.log('[A] tokens =', JSON.stringify(joined))
console.log('[A] tokenCount =', tokens.length)
console.log('[A] interrupt =', JSON.stringify(interrupt))
console.log('[A] done =', JSON.stringify(done))
console.log('[A] error =', JSON.stringify(err))
const ok = joined === '你好' && tokens.length === 2 && interrupt !== null && done !== null && err !== null
console.log('[A-RESULT]', ok ? 'PASS' : 'FAIL')
process.exit(ok ? 0 : 1)
