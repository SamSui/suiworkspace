/**
 * P2 模块占位页（ADR §5.5/5.6/5.8）：仅导航 + 模块定位 + P2 徽章，不实现、不伪装可用。
 */
export default function P2PlaceholderPage({ module }: { module: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{module}</h1>
      <div className="rounded-card bg-white p-6 shadow-card">
        <span className="inline-flex items-center rounded-full bg-accent-purple/10 px-3 py-1 text-xs text-accent-purple">
          P2 规划中
        </span>
        <ul className="mt-4 space-y-2 text-sm text-gray-500">
          {module === '模型配置' && (
            <>
              <li>· 统一 LLM 接入、API Key 管理、路由</li>
              <li>· 超时 / 重试 / 熔断 / Token 统计、配额、审计（LLM Gateway）</li>
            </>
          )}
          {module === '工具 - MCP' && (
            <>
              <li>· 工具统一接入（HTTP / MCP / SQL）</li>
              <li>· 权限、超时、审计</li>
            </>
          )}
          {module === '用户与租户权限' && (
            <>
              <li>· Tenant → User → Role → Permission 分级管理（RBAC）</li>
            </>
          )}
        </ul>
        <p className="mt-4 text-xs text-gray-400">
          本期仅导航占位 + 模块定位；随 P2 实现（后端无对应 API，不虚构）。
        </p>
      </div>
    </div>
  )
}