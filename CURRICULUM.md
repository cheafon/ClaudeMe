# Curriculum — 从零实现 Coding Agent

26 个里程碑，每个里程碑是一个独立的 Python 脚本。从单轮对话开始，逐步复现 Claude Code 的核心机制。

---

| # | 名称 | 要点 | 验收标准 |
|---|------|------|----------|
| M1 | 流式对话 | 单轮 streaming 调用 | 能看到字符逐字输出 |
| M2 | 多轮 REPL + System Prompt | while 循环 + 消息历史 + 加载 FAN.md | 多轮对话不失忆 |
| M3 | 工具定义 + Agentic Loop | 定义 read_file → 解析 tool_use → 执行 → 结果回传 → 循环 | 模型能读文件 |
| M4 | 文件工具扩展 | write_file + glob + grep | 模型能读写搜索文件 |
| M5 | Edit 工具 | diff-based 字符串替换 + 唯一性校验 | 模型能精确编辑文件 |
| M6 | Bash 工具 + 权限系统 | subprocess 执行命令 + allow/deny/ask 三级权限 | 危险命令弹出确认 |
| M7 | 会话持久化 | JSONL 追加写入 + --resume 加载恢复 | 重启后能继续上次对话 |
| M8 | 上下文压缩 | 检测 token 接近上限 → 自动摘要历史消息 | 长对话不爆 context |
| M9 | 配置加载 | settings.json 多文件优先级链 + deep merge | 从配置文件读取模型/参数 |
| M10 | Slash 命令 + Token 追踪 | /help /cost /model /status + 累计 usage 统计 | /cost 显示费用 |
| M11 | Hook 系统 | PreToolUse/PostToolUse 子进程 stdin/stdout JSON | hook 脚本能拦截工具调用 |
| M12 | 插件 + MCP | 动态加载外部工具定义 + 连接 MCP server | 加载外部工具 |
| M13 | 并行工具调用 | 解析一次响应中的多个 tool_use + 批量执行 | 模型一次调用多个工具 |
| M14 | Git 上下文注入 | 启动时执行 git status/diff/log，注入 system prompt | agent 感知代码库状态 |
| M15 | 动态 System Prompt | FAN.md + git 信息 + 目录树 + 时间，每次重新生成 | system prompt 随环境变化 |
| M16 | Bash 安全验证 | 命令模式匹配，自动识别危险命令 | rm -rf 等被自动拦截 |
| M17 | Agentic Loop 进化 | 可配置 max_iterations + 迭代计数器 + 超限安全退出 | 设 max_iterations=3，第 4 轮自动停止 |
| M18 | Extended Thinking | 解析 thinking 块 + 流式展示思考过程 | 模型先显示思考再输出结果 |
| M19 | Sub-agent 派发 | Agent 工具：spawn 子线程运行独立 agent + 工具白名单 | 主 agent 派发子任务并返回结果 |
| M20 | 错误恢复 | FailureScenario 分类 + RecoveryRecipe 自动修复 + 升级策略 | 工具失败自动重试，超限升级人工 |
| M21 | Sandbox 执行 | 文件系统隔离（WorkspaceOnly）+ 路径边界校验 | bash 命令只能访问工作区内文件 |
| M22 | WebFetch + WebSearch | 抓取网页内容 + 互联网搜索 | 模型能搜索并抓取网页 |
| M23 | Plan Mode | EnterPlanMode/ExitPlanMode 只读探索 + 规划 | /plan 进入规划模式，写操作被禁用 |
| M24 | Task 管理 | TodoWrite + TaskCreate/Update/List 任务清单 | 模型能列任务清单、打勾 |
| M25 | ToolSearch | 工具延迟加载，按需搜索加载 schema | 几十个工具按需加载，不撑爆 prompt |
| M26 | Memory 读写 | 跨会话记忆存储 + frontmatter 解析 + 索引 | agent 记住用户偏好 |

---

## 参考资料

`claude-code-sourcemap/restored-src/src/` — Claude Code 2.1.88 还原 TypeScript 源码，每个里程碑的设计思想都能在这里找到对应实现。

## 如何开始

1. 复制 `py-agent/.env.example` 为 `py-agent/.env`，填入你的 API key
2. 打开 Claude Code，直接说"开始学习"
3. Claude 会从 M1 开始生成脚手架，你只需填写 TODO
