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
// ---- 真实后端契约（对齐 langgraph_service/sse.py）：token data 形为 {"seq":N,"text":"..."}，
//      不是 {token:...}；done 只回 message_id+usage，不回 thread_id。----
const parts = [
  ev('token', '{"seq":1,"text":"你"}'),
  ev('token', '{"seq":2,"text":"好"}'),
  ev('interrupt', '{"reason":"需要确认"}'),
  ev('done', '{"message_id":1,"usage":{"prompt":1,"completion":1}}'),
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
const donePayload = (done as { thread_id?: unknown } | null) ?? {}
// 真实后端 done 不带 thread_id（sse.py 只回 message_id+usage），前端不得依赖它。
const hasServerTid = donePayload.thread_id != null
console.log('[A] tokens =', JSON.stringify(joined))
console.log('[A] tokenCount =', tokens.length)
console.log('[A] interrupt =', JSON.stringify(interrupt))
console.log('[A] done =', JSON.stringify(done))
console.log('[A] doneHasServerTid =', hasServerTid)
console.log('[A] error =', JSON.stringify(err))
const ok =
  // 真实契约 {"seq":N,"text":"..."} 喂入必须得「你好」——锁定 Fix1，杜绝 false-green
  joined === '你好' &&
  tokens.length === 2 &&
  interrupt !== null &&
  done !== null &&
  err !== null
console.log('[A-RESULT]', ok ? 'PASS' : 'FAIL')
process.exit(ok ? 0 : 1)
