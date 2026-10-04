/**
 * 对话 store 级用例（Fix2 验收）：
 * 真实后端契约下 `done` 不带 thread_id，且新会话可能只有 `interrupt`（待人工确认）流。
 * 断言：客户端自生成 thread_id 后，新会话即可经 `resolveHitl` 携带该 id 调 `/v1/chat/resume`。
 */
import { useChatStore } from '../../src/store/chat'

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

// 记录 resume 是否被触发、带哪个 thread_id
let resumeHit: { thread_id: string | null; value: string | null } | null = null

// mock fetch：区分 stream 与 resume 两个端点路由；body 取第二入参的 init.body
globalThis.fetch = (async (
  input: RequestInfo | URL,
  init?: { body?: BodyInit | null },
) => {
  const url = String(input)
  if (url.includes('/v1/chat/resume')) {
    // 捕获 resume 请求体（此为 Fix2 关键断言目标）
    let body: { thread_id?: string; value?: string } = {}
    try {
      body = JSON.parse(String(init?.body ?? '')) as { thread_id?: string; value?: string }
    } catch {
      body = {}
    }
    resumeHit = { thread_id: body.thread_id ?? null, value: body.value ?? null }
    return new Response(sse([ev('done', '{"message_id":9}')]), { status: 200 })
  }
  // stream：发 interrupt（待人工确认）后保持流开，不给 done —— 模拟 HITL 挂起
  return new Response(sse([ev('interrupt', '{"reason":"human_approval"}')]), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}) as typeof fetch

async function run() {
  const tid = useChatStore.getState().newThread()
  useChatStore.getState().setActive(tid)
  await useChatStore.getState().sendMessage('需要你确认这件事')

  const thread = useChatStore.getState().threads.find((t) => t.id === tid)
  const tail = thread?.messages.slice(-1)[0]
  const nativeTid = thread?.thread_id ?? null

  // Fix2 断言：新会话无需后端回回 thread_id，客户端生成后即可 resume
  const gotTid = typeof nativeTid === 'string' && nativeTid.length > 0
  const awaitingHitl = tail?.status === 'waiting_hitl'
  if (gotTid && awaitingHitl) {
    await useChatStore.getState().resolveHitl(tid, '接受')
  }
  const resumeOk =
    resumeHit !== null &&
    resumeHit.thread_id === nativeTid &&
    resumeHit.value === '接受'
  const ok = gotTid && awaitingHitl && resumeOk
  console.log('[H] thread_id =', nativeTid)
  console.log('[H] tailStatus =', tail?.status)
  console.log('[H] resume =', JSON.stringify(resumeHit))
  console.log('[B-RESULT]', ok ? 'PASS' : 'FAIL')
  return ok
}

const ok = await run()
process.exit(ok ? 0 : 1)