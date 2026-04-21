# CLAUDE.md
这是一个从零复现 Claude Code 核心机制的自学仓库。
---

## py-agent/ — 用户自学 Python Agent 项目

### 目标
用户从零用 Python 实现一个 coding agent，逐步靠近 Claude Code 的功能。
参考资料：`claude-code-sourcemap/restored-src/src/` 是答案书（Claude Code 2.1.88 还原 TypeScript 源码）。

### 代码位置
`py-agent/` 目录，在本仓库根目录下。

### 命名约定
- **`fancode`** 是 agent 本身的名字（对标 `claude code`），在教程说明、system prompt、用户交互描述中用这个名字
- **`fan`** 只用于配置文件名（`FAN.md` 对标 `CLAUDE.md`、`fan.py` 对标入口命令 `claude`）
- 根目录保持 `py-agent/`，这是工程目录名，不受此约定影响

### 模块结构约定
```
py-agent/
├── FAN.md            # 项目配置（对标 CLAUDE.md）
└── milestones/       # 每个里程碑的独立可运行脚本（永续迭代，无整合终点）
    ├── m1_hello.py
    ├── ...
    └── mXX_xxx.py    # 当前最新里程碑
```

> 每个里程碑都是单文件，不存在 fan.py 整合节点，迭代没有尽头。里程碑列表维护在当前项目的 memory/project_pyagent_progress.md。

### API 配置
- 默认使用 Anthropic API，也兼容任何支持 Anthropic 协议的代理（如 Minimax）
- 配置在 `py-agent/.env` 中，参考 `py-agent/.env.example`
- py-agent 用 `python-dotenv` 读取 `py-agent/.env`

### 首次运行引导
**检测条件：当前项目 memory 中不存在 `project_pyagent_progress.md`**

触发后执行以下步骤（**按顺序，不可跳过**）：

1. 读取项目根目录的 `CURRICULUM.md`，从中解析 26 个里程碑的名称、要点、验收标准
2. 在当前项目 memory 目录创建 `project_pyagent_progress.md`，内容格式参照进度文件模板：M1 状态为 ✅ 完成（已预置实现），其余里程碑状态为 ⬚ 待开始，当前里程碑为 M2
3. 同时创建 `MEMORY.md` 索引，指向该进度文件
4. **删除 `CURRICULUM.md`**（已写入 memory，不再需要）
5. **直接开始 M1。**

### Claude 的工作方式（每次会话）
1. 读取当前项目 memory 中的 `project_pyagent_progress.md`，了解当前里程碑和进度
2. 帮用户理解概念，用户自己写代码
3. 生成给用户写代码的文件时：以 `py-agent/milestone_base.py` 为起点完整复制，再在此基础上添加新功能；所有 UI 代码（class S、spinner、_prompt、_print_banner）不得修改；生成后立即 `uv run` 验证无报错
   - 文件头部（/// script 块之后）先用 3-5 行注释说清楚：上一个里程碑的局限是什么、这个里程碑解决什么问题、核心思路是什么；然后再列完成后能做到的事情
   - TODO 注释风格：每个步骤说明后面留一行空注释 `#    TODO: （下面写你的代码）`，让用户可以用编辑器搜索 `TODO` 快速定位所有待填位置
   - TODO 详细程度：涉及陌生库的 API（方法名、参数格式不直觉）→ 直接在注释里写出可运行的代码行；熟悉的 Python 模式（if/else、try/except、字符串拼接）→ 只写一句话描述意图，不写代码
   - 不在注释里写"对标 TS xxx 行"之类的引用，用户无需知道源码位置
   - 生成里程碑文件后，在 `.vscode/launch.json` 的 `configurations` 数组里追加：`{"name": "Mx: 名称", "type": "debugpy", "request": "launch", "program": "${workspaceFolder}/py-agent/milestones/mx_xxx.py", "python": "${workspaceFolder}/py-agent/.venv/bin/python", "console": "integratedTerminal", "cwd": "${workspaceFolder}/py-agent"}`
   - 脚手架生成完毕后，用以下格式告知用户下一步选项：
     ```
     ---
     **接下来你可以：**
     - `cd py-agent && uv run milestones/mx_xxx.py` — 直接运行，看看效果
     - `/fan-hint` — 卡住了？获取一个小提示
     - `/fan-checkit` — 写完了？检查完成度
     - 或者随时跟我讨论
     ---
     ```
4. 诊断报错时：先读用户已写的代码，指出哪里写错或写得不完整，不要直接给出完整答案；对比 `claude-code-sourcemap/restored-src/src/` 里的实现作为参考

### 里程碑选题原则
聚焦 agent 核心机制（思考、派发、恢复、隔离、感知），不要安排纯工程打磨类课题（成本计算、配置验证、压缩预算、信任策略、权限层）。用户学习目标是理解 agent 的核心架构，工程细节可以自学。
