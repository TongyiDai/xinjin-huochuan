---
name: agent-relay
description: "薪尽火传 / Agent Relay：让 Codex、TRAE CLI、Claude Code、Cursor 等不同 AI Agent 在同一项目上可靠接棒，携带经过验证的状态、决定、产物、阻塞、验收标准和下一步继续工作。用户提到切换 Agent、继续另一个 Agent 的工作、跨 Agent 接力、交棒/接棒、检查其他 Agent 新进展、限额或中断恢复、并行协作、共享项目上下文、handoff、relay、接力棒时使用。"
---

# 薪尽火传 · Agent Relay

## 首步：检查安装与状态边界

新环境先运行 `python3 scripts/doctor.py --json`。它只检查代码，不触碰
`~/.agent-relay`。涉及真实项目时，再调用 `relay_context` 或运行 CLI 的
`doctor --hub <approved-hub>`；不要在没有项目路径时全量扫描 Home 目录。

安装、MCP 和 Hook 的差异见 [runtime.md](references/runtime.md)。

换 Agent，不断档。Memory Hub 是内部存储层；Relay 才是产品层。

## 强制首步

触发本 Skill 后，先读取 Relay Context，不要先扫描项目目录、Skill 文件或 Home 目录：

1. 优先调用 MCP 工具 `relay_context`，参数为当前项目路径和当前 Agent 名。
2. MCP 不可用时运行 `agent-relay context --agent <name> --project "$PWD"`。
3. 只有 Context 指向具体产物后，才打开这些产物做现场验证。

不要用 `find /`、全量目录扫描或原始会话日志代替 Relay Context。

## 动作选择

| 用户意图 | 动作 |
| --- | --- |
| “继续”“接着做”“看看其他 Agent 做了什么” | `context` 或 `resume --no-mark`，检查 `inbox` |
| “交给 Codex/Claude/TRAE” | `offer` |
| 接棒开始做 | `accept` |
| 长任务仍在进行 | `heartbeat` |
| 做完并附证据 | `complete` |
| 交棒方确认完成 | `verify` |
| 无法继续 | `fail` 或 `reject` |
| 取消接力 | `cancel` |
| 一般阶段记录 | `capture` |

使用 `agent-relay` 命令；旧 `memory-hub` 仅为兼容入口。

## 接棒

1. 读取当前项目与待接棒队列。
2. 打开交棒方引用的真实产物。
3. 产物缺失或已变化时，不要直接接棒；先说明差异。
4. 确认可执行后 `accept`。
5. 不要求用户复述 Hub 已记录的背景。

```bash
agent-relay context --agent codex --project "$PWD"
agent-relay inbox --agent codex --project "$PWD"
agent-relay accept \
  --relay relay-xxxx \
  --agent codex \
  --summary "已核对输入产物，开始接棒"
```

在只读沙箱里先运行：

```bash
agent-relay resume --agent codex --project "$PWD" --no-mark
```

## 交棒

交棒必须包含明确验收标准。提供真实产物路径或可复核来源；不要只写“已经完成”。

```bash
agent-relay offer \
  --project "$PWD" \
  --from-agent trae \
  --to-agent codex \
  --summary "实现完成，交给 Codex 做边界验证" \
  --acceptance "测试全部通过" \
  --acceptance "不存在敏感信息" \
  --artifact "/path/to/implementation" \
  --source-ref "test: integration suite passed" \
  --priority high
```

默认交棒 7 天未接收自动过期；接棒后默认租约 1 小时。长任务定期续租：

```bash
agent-relay heartbeat \
  --relay relay-xxxx \
  --agent codex \
  --summary "边界测试进行中"
```

## 完成与验收

接棒方完成时必须附证据：

```bash
agent-relay complete \
  --relay relay-xxxx \
  --agent codex \
  --summary "边界验证完成" \
  --artifact "/path/to/test-results.json"
```

验收方逐条确认标准并核对产物：

```bash
agent-relay verify \
  --relay relay-xxxx \
  --agent trae \
  --summary "产物和验收标准均已确认" \
  --accept-all-criteria
```

`completed` 还不算闭环，只有 `verified` 才算一棒完成。

## 记录边界

- 不保存整段聊天、密码、token、API Key 或未筛选隐私数据。
- 不把猜测写成事实；不修改旧事件，追加纠正记录。
- 单 Agent、单轮、无后续价值的任务不写 Relay。
- Relay 不替代 Git、业务版本控制或多人同时编辑的文件锁。
- 真实环境验证优先于记忆内容。

完整状态机、依赖链、产物指纹、冲突与隐私规则见 [references/protocol.md](references/protocol.md)。
