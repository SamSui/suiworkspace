/**
 * 监控（ADR §5.7）：指标嵌入关键卡 + 超链接 Grafana。
 * 本期对接 /metrics + Grafana dashboard（存在则页面内嵌示例指标卡），不再造仪表。
 */
import { useState } from 'react'

export default function MonitorPage() {
  const [range, setRange] = useState('1m')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">运行监控</h1>
        <div className="ml-auto flex gap-1">
          {['1m', '5m', '30m'].map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`rounded ctrl px-3 py-1 text-sm ${
                range === r ? 'bg-primary text-white' : 'border border-gray-300 text-gray-600'
              }`}
            >
              近{r}
            </button>
          ))}
        </div>
      </div>

      {/* 指标嵌入卡（占位骨架；生产对接 /metrics + Grafana 面板） */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'API QPS', value: '—' },
          { label: '响应 P95', value: '—' },
          { label: '错误率', value: '—' },
          { label: '文档处理成功率', value: '—' },
        ].map((m) => (
          <div key={m.label} className="rounded-card bg-white p-5 shadow-card">
            <p className="text-sm text-gray-500">{m.label}（{range}）</p>
            <p className="mt-1 text-2xl font-semibold text-primary">{m.value}</p>
            <p className="mt-1 text-xs text-gray-300">需接入 /metrics 后回填</p>
          </div>
        ))}
      </div>

      {/* Grafana 嵌入占位 + 深入链接 */}
      <div className="rounded-card bg-white p-5 shadow-card">
        <div className="flex items-center justify-between">
          <h2 className="font-medium">Grafana 面板（指标嵌入）</h2>
          <span className="text-xs text-gray-400">
            项目内 <code>deploy/grafana/agent-platform-dashboard.json</code>
          </span>
        </div>
        <div className="mt-4 flex h-64 items-center justify-center rounded ctrl border border-dashed border-gray-300 text-sm text-gray-400">
          Grafana embed 面板占位（随部署后 iframe 嵌入）
        </div>
        <p className="mt-2 text-xs text-gray-400">
          trace_id 检索与面板关联后续接入；本期先落地关键指标卡容器。
        </p>
      </div>
    </div>
  )
}