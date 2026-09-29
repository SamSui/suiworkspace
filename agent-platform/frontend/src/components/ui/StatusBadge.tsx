/**
 * 状态徽章：文档/任务状态机 + 消息流态（ADR §决策3 / §6.1）。
 * document.status：0 未处理(灰) 1 处理中(蓝) 2 完成(绿) 3 失败(红)。
 */
import type { MessageStatus } from '../../store/chat'

const DOC_STATES: Array<{ label: string; cls: string; dot: string }> = [
  { label: '未处理', cls: 'bg-gray-100 text-gray-600', dot: 'bg-gray-400' },
  { label: '处理中', cls: 'bg-blue-50 text-blue-600', dot: 'bg-blue-500' },
  { label: '完成', cls: 'bg-green-50 text-green-600', dot: 'bg-success' },
  { label: '失败', cls: 'bg-red-50 text-red-600', dot: 'bg-danger' },
]

/** 文档/任务状态徽章 */
export function DocStatusBadge({ status }: { status: number }) {
  const s = DOC_STATES[status] ?? DOC_STATES[0]
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  )
}

const MSG_STATUS_TEXT: Record<MessageStatus, string> = {
  streaming: '● 生成中',
  done: '',
  error: '',
  waiting_hitl: '● 待人工确认',
  stopped: '（已停止）',
}
const MSG_STATUS_CLS: Record<MessageStatus, string> = {
  streaming: 'text-blue-600',
  done: 'text-gray-500',
  error: 'text-danger',
  waiting_hitl: 'text-accent',
  stopped: 'text-gray-400',
}

/** 消息流增量态标（streaming 时驻留气泡内，ADR 决策2） */
export function StreamingDot({ status }: { status: MessageStatus }) {
  const text = MSG_STATUS_TEXT[status] ?? ''
  if (!text) return null
  return (
    <span className={`text-xs ${MSG_STATUS_CLS[status]}`}>{text}</span>
  )
}
