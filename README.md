# suiworkspace — 随工作室 团队智能体配置库

随工作室将 6 个团队角色落成为 Multica 智能体。本仓库存放每个角色的配置文件（按《Agent 配置文件规范 v5.4 · SPEC-AGENT-CONFIG-001》生成）。

## 目录结构

```
agents/
├─ pm/         随产品 💡   产品经理 —— 需求与价值负责人（Orchestrator）
├─ architect/  随架构 🦉   架构师 —— 总调度与质量裁决者（Orchestrator）
├─ designer/   随设计 🎨   交互/UI 设计师 —— 用户体验与交互设计负责人
├─ developer/  随研发 🛠️   研发工程师 —— 技术实施负责人
├─ tester/     随测试 🔍   测试/QA —— 质量验证负责人
└─ main/       Mika 🦄     总协调 / Chief Assistant（由现有 Mika 承接）
```

每个角色目录含三个文件：
- `SOUL.md` — Identity Layer：身份、人格、价值观、沟通风格
- `AGENTS.md` — Execution Layer（source of truth）：执行规则、安全、阻塞上报、内嵌协作协议
- `IDENTITY.md` — 身份元数据（Name / Role / Style / Emoji）

## 协议内嵌

- **Orchestrator**（pm / architect / main）→ `AGENTS.md` 内嵌 **Team Orchestration Protocol**（附录 D）
- **非 Orchestrator**（designer / developer / tester）→ 内嵌 **Subagent Execution Protocol**（附录 E）

## 协作基础约定

- 架构师是唯一总调度角色，负责阶段准入、优先级冲突与跨角色协调。
- 每个阶段必须经过架构师审核；方案评分 ≥80 才进入研发。
- 发现问题优先由当前责任角色直接闭环，仅在跨角色 / 技术路线 / 里程碑等场景升级。
- 发布前由 QA 给测试结论，架构师确认风险与回滚后，由研发完成合并与发布。