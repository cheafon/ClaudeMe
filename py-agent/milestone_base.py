# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic", "python-dotenv", "httpx", "beautifulsoup4", "ddgs"]
# ///
#
# milestone_base.py — 里程碑基础模板（UI + 基础设施，以 M23 为基准）
#
# 生成新里程碑时，直接从本文件复制全部内容，再在顶部加里程碑说明注释，
# 在底部（execute_tool / handle_slash_command / build_system_prompt）添加新功能。
# 禁止修改本文件中已有的 UI 代码（class S、_fmt_*、make_spinner、_prompt、_print_banner）。

import os
import sys
import json
import glob
import readline  # noqa: F401
import re
import subprocess
import time
import threading
import itertools
import uuid
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import anthropic
import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

# ── ANSI 颜色 ────────────────────────────────────────────────
class S:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    ITALIC  = "\033[3m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    RED     = "\033[31m"
    CYAN    = "\033[36m"
    GRAY    = "\033[90m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"

    TOOL     = f"{GREEN}{BOLD}"
    TOOL_DIM = f"{GRAY}"
    ERR      = f"{RED}{BOLD}"
    WARN     = f"{YELLOW}"
    THINK    = f"{GRAY}{ITALIC}"
    PROMPT   = f"{BLUE}{BOLD}"
    SUCCESS  = f"{GREEN}"
    LABEL    = f"{DIM}"
    PLAN     = f"{MAGENTA}{BOLD}"   # plan mode 专用颜色

def _fmt_tool_input(name: str, tool_input: dict) -> str:
    # 新里程碑：在此追加新工具的摘要格式，其余行不动
    if name == "bash":            return tool_input.get("command", "")
    if name == "read_file":       return tool_input.get("path", "")
    if name == "write_file":      return f"{tool_input.get('path','')} ({len(tool_input.get('content',''))} chars)"
    if name == "edit_file":       return tool_input.get("path", "")
    if name == "glob_search":     return tool_input.get("pattern", "")
    if name == "grep_search":     return f"{tool_input.get('pattern','')} in {tool_input.get('path','.')}"
    if name == "agent":           return tool_input.get("description", "")
    if name == "web_fetch":       return tool_input.get("url", "")
    if name == "web_search":      return tool_input.get("query", "")
    if name == "enter_plan_mode": return ""
    if name == "exit_plan_mode":  return tool_input.get("plan", "")[:60]
    return json.dumps(tool_input, ensure_ascii=False)[:60]

def _fmt_result_summary(result: str, max_lines: int = 5) -> str:
    lines = result.split("\n")
    if len(lines) <= max_lines:
        return result
    shown = "\n".join(lines[:max_lines])
    return f"{shown}\n{S.DIM}  ... ({len(lines) - max_lines} more lines){S.RESET}"

# ── .env 鉴权 ────────────────────────────────────────────────
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# ── 配置加载 ─────────────────────────────────────────────────
FAN_CONFIG_PATHS = [
    Path.home() / ".fan" / "settings.json",
    Path.cwd() / ".fan" / "settings.json",
    Path.cwd() / ".fan" / "settings.local.json",
]

DEFAULTS = {
    "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
    "max_tokens": 3000,
    "max_iterations": 32,
    "compact": {"threshold_tokens": 6000, "preserve_recent_messages": 4},
    "hooks": {"pre_tool_use": [], "post_tool_use": []},
    "mcpServers": {},
    "recovery": {"max_attempts": 2},
    "sandbox": {"enabled": True},
    "web": {"max_fetch_chars": 20_000, "max_search_results": 5, "timeout_sec": 15},
}

def deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key in override:
        if key in result and isinstance(result[key], dict) and isinstance(override[key], dict):
            result[key] = deep_merge(result[key], override[key])
        else:
            result[key] = override[key]
    return result

def load_config() -> dict:
    cfg = DEFAULTS.copy()
    for p in FAN_CONFIG_PATHS:
        if p.exists():
            try:
                cfg = deep_merge(cfg, json.loads(p.read_text()))
            except Exception:
                pass
    return cfg

CONFIG = load_config()
model = CONFIG["model"]
max_tokens = CONFIG["max_tokens"]
MAX_ITERATIONS = CONFIG["max_iterations"]
MAX_RECOVERY_ATTEMPTS = CONFIG["recovery"]["max_attempts"]
SANDBOX_ENABLED = CONFIG["sandbox"]["enabled"]
MAX_FETCH_CHARS = CONFIG["web"]["max_fetch_chars"]
MAX_SEARCH_RESULTS = CONFIG["web"]["max_search_results"]
WEB_TIMEOUT = CONFIG["web"]["timeout_sec"]

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    base_url=os.environ.get("ANTHROPIC_BASE_URL"),
)

# ── 沙盒边界 ─────────────────────────────────────────────────
WORKSPACE_ROOT: Path = Path.cwd().resolve()

def is_within_workspace(path_str: str) -> bool:
    p = Path(path_str)
    resolved = p.resolve() if p.is_absolute() else (WORKSPACE_ROOT / p).resolve()
    return resolved.is_relative_to(WORKSPACE_ROOT)

def check_file_path(path_str: str, op_type: str = "read") -> str | None:
    if not SANDBOX_ENABLED:
        return None
    if not is_within_workspace(path_str):
        return f"[Sandbox] 拒绝 {op_type} 访问：路径 '{path_str}' 超出工作区"
    return None

def extract_paths_from_bash(command: str) -> list[str]:
    candidates = re.findall(r'(?<!\w)(/[^\s\'";&|><]+)', command)
    return list(dict.fromkeys(p for p in candidates if p != "/dev/null" and not p.startswith("/-")))

def check_bash_paths(command: str) -> str | None:
    if not SANDBOX_ENABLED:
        return None
    for path in extract_paths_from_bash(command):
        if not is_within_workspace(path):
            return f"[Sandbox] 拒绝执行：命令包含工作区外路径 '{path}'"
    return None

# ── Web 工具 ─────────────────────────────────────────────────
web_stats = {"fetch_count": 0, "fetch_bytes": 0, "search_count": 0}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

def fetch_url(url: str) -> dict:
    try:
        response = httpx.get(url, follow_redirects=True, timeout=WEB_TIMEOUT, headers={"User-Agent": USER_AGENT})
    except Exception as e:
        return {"content": f"[FetchError] {e}", "code": 0, "bytes": 0, "url": url}
    status_code = response.status_code
    raw_bytes = len(response.content)
    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        content = soup.get_text(separator="\n", strip=True)
    else:
        content = response.text
    content = content[:MAX_FETCH_CHARS]
    web_stats["fetch_count"] += 1
    web_stats["fetch_bytes"] += raw_bytes
    return {"content": content, "code": status_code, "bytes": raw_bytes, "url": str(response.url)}

def search_web(query: str, max_results: int = MAX_SEARCH_RESULTS) -> str:
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results)
    except Exception as e:
        return f"[SearchError] {e}"
    if not results:
        return "未找到相关结果"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    URL: {r['href']}")
        lines.append(f"    摘要: {r['body']}")
        lines.append("")
    web_stats["search_count"] += 1
    return "\n".join(lines)

# ── FAN.md ────────────────────────────────────────────────────
def load_fan_md() -> str:
    for name in ("FAN.md", "fan.md"):
        p = Path.cwd() / name
        if p.exists():
            return p.read_text()
    return ""

# ── Bash 安全验证 ─────────────────────────────────────────────
DANGER_PATTERNS = [
    (re.compile(r'\brm\s+-[rf]{1,2}\s+/'), "block", "拒绝：rm -rf 目标为根目录"),
    (re.compile(r'\bdd\b.*\bof=/dev/'), "block", "拒绝：dd 写入设备文件"),
    (re.compile(r'>\s*/etc/'), "block", "拒绝：重定向到 /etc/"),
    (re.compile(r'\bchmod\s+777\b'), "warn", "警告：chmod 777"),
    (re.compile(r'\bsudo\b'), "warn", "警告：sudo 提权"),
    (re.compile(r'\bcurl\b.*\|\s*bash'), "warn", "警告：curl pipe bash"),
]

def validate_command(command: str) -> tuple[str, str]:
    for pattern, verdict, msg in DANGER_PATTERNS:
        if pattern.search(command):
            return verdict, msg
    return "allow", ""

# ── MCP 占位 ─────────────────────────────────────────────────
def execute_mcp_tool(server_name: str, tool_name: str, tool_input: dict) -> str:
    return f"[MCP Error] MCP server '{server_name}' 未连接"

# ── 工具定义（M23 基准，新里程碑追加） ───────────────────────
BUILTIN_TOOLS: list[dict] = [
    {
        "name": "bash",
        "description": "在工作区内执行 bash 命令",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "number"}}, "required": ["command"]},
    },
    {
        "name": "read_file",
        "description": "读取工作区内的文件内容",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": "向工作区内的文件写入内容",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    },
    {
        "name": "edit_file",
        "description": "替换文件中的字符串片段",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}}, "required": ["path", "old_string", "new_string"]},
    },
    {
        "name": "glob_search",
        "description": "按 glob 模式搜索文件",
        "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]},
    },
    {
        "name": "grep_search",
        "description": "在工作区内搜索文本",
        "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}}, "required": ["pattern"]},
    },
    {
        "name": "web_fetch",
        "description": "抓取指定 URL 的网页内容，提取正文文字",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "web_search",
        "description": "在互联网上搜索关键词，返回标题、链接、摘要列表",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
    },
    {
        "name": "enter_plan_mode",
        "description": "进入 plan mode：只读探索阶段，禁止写操作",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "exit_plan_mode",
        "description": "提交计划并请求用户批准，批准后退出 plan mode 开始实施",
        "input_schema": {
            "type": "object",
            "properties": {"plan": {"type": "string", "description": "你制定的实施计划，用 Markdown 格式写清楚步骤"}},
            "required": ["plan"],
        },
    },
]

AGENT_TOOL: dict = {
    "name": "agent",
    "description": "派发子 agent 完成独立任务",
    "input_schema": {"type": "object", "properties": {"description": {"type": "string"}, "prompt": {"type": "string"}, "subagent_type": {"type": "string", "enum": ["explore", "general"]}}, "required": ["description", "prompt"]},
}

TOOLS = BUILTIN_TOOLS + [AGENT_TOOL]

# ── 工具执行层 ────────────────────────────────────────────────
def execute_tool(name: str, tool_input: dict) -> str:

    if name == "agent":
        return execute_agent_tool(tool_input)

    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            _, server_name, raw_tool_name = parts
            return execute_mcp_tool(server_name, raw_tool_name, tool_input)
        return f"MCP 工具名格式错误: {name}"

    if name == "web_fetch":
        url = tool_input.get("url", "")
        if not url:
            return "[web_fetch] 缺少 url 参数"
        result = fetch_url(url)
        return f"[{result['code']}] {result['url']}  ({result['bytes']} bytes)\n\n{result['content']}"

    if name == "web_search":
        query = tool_input.get("query", "")
        if not query:
            return "[web_search] 缺少 query 参数"
        return search_web(query, max_results=int(tool_input.get("max_results", MAX_SEARCH_RESULTS)))

    if name == "bash":
        command = tool_input["command"]
        timeout_sec = tool_input.get("timeout", 30)
        verdict, message = validate_command(command)
        if verdict == "block":
            return message
        elif verdict == "warn":
            print(f"  {S.WARN}⚠ {message}{S.RESET}")
            if input(f"  {S.WARN}确认执行吗？y/N{S.RESET} ").lower() != "y":
                return "用户拒绝此命令"
        block = check_bash_paths(command)
        if block:
            return block
        try:
            obj = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            return "命令超时"
        if not obj.stdout and not obj.stderr:
            return "(no output)"
        return obj.stdout + ("\n[stderr]\n" + obj.stderr if obj.stderr else "")

    if name == "read_file":
        block = check_file_path(tool_input["path"], "read")
        if block:
            return block
        try:
            return Path(tool_input["path"]).read_text()
        except Exception as e:
            return f"读取失败: {e}"

    if name == "write_file":
        block = check_file_path(tool_input["path"], "write")
        if block:
            return block
        try:
            path = Path(tool_input["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tool_input["content"])
            return f"已写入 {tool_input['path']}"
        except Exception as e:
            return f"写入失败: {e}"

    if name == "edit_file":
        block = check_file_path(tool_input["path"], "write")
        if block:
            return block
        try:
            text = Path(tool_input["path"]).read_text()
        except Exception as e:
            return f"读取失败: {e}"
        old_string, new_string = tool_input["old_string"], tool_input["new_string"]
        if old_string == new_string:
            return "old_string 和 new_string 相同，无需修改"
        if old_string not in text:
            return "old_string 在文件中不存在"
        replace_all = tool_input.get("replace_all", False)
        text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        Path(tool_input["path"]).write_text(text)
        return "已替换完成"

    if name == "glob_search":
        search_root = tool_input.get("path", ".")
        block = check_file_path(search_root, "read")
        if block:
            return block
        result = glob.glob(tool_input["pattern"], recursive=True, root_dir=search_root)
        return "\n".join(result) if result else "无匹配结果"

    if name == "grep_search":
        search_root = tool_input.get("path", ".")
        block = check_file_path(search_root, "read")
        if block:
            return block
        cmd = ["grep", "-r", "-n", tool_input["pattern"], search_root]
        if "glob" in tool_input:
            cmd += [f"--include={tool_input['glob']}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout if result.stdout else "无匹配结果"

    return f"未知工具: {name}"

# ── Sub-agent ────────────────────────────────────────────────
AGENT_STORE = Path.cwd() / ".fan" / "agents"
SUBAGENT_ALLOWED_TOOLS = {
    "explore": {"read_file", "glob_search", "grep_search", "bash"},
    "general": {t["name"] for t in BUILTIN_TOOLS},
}

def iso8601_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def make_agent_id() -> str:
    return uuid.uuid4().hex[:12]

def write_manifest(manifest: dict) -> None:
    AGENT_STORE.mkdir(parents=True, exist_ok=True)
    (AGENT_STORE / f"{manifest['agent_id']}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

def run_agent_job(manifest: dict, prompt: str, allowed_tools: set[str]) -> None:
    sub_tools = [t for t in BUILTIN_TOOLS if t["name"] in allowed_tools]
    iterations = 0
    sub_messages = [{"role": "user", "content": prompt}]
    collected_text = []
    try:
        while iterations < 10:
            iterations += 1
            response = client.messages.create(model=model, max_tokens=max_tokens, messages=sub_messages, tools=sub_tools)
            sub_messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "text":
                    collected_text.append(block.text)
            if response.stop_reason == "end_turn":
                break
            elif response.stop_reason == "tool_use":
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                results = [{"type": "tool_result", "tool_use_id": b.id, "content": execute_tool(b.name, b.input)} for b in tool_uses]
                sub_messages.append({"role": "user", "content": results})
        manifest["status"] = "completed"
        (AGENT_STORE / f"{manifest['agent_id']}.md").write_text("\n".join(collected_text))
    except Exception as e:
        manifest["status"] = "failed"
        (AGENT_STORE / f"{manifest['agent_id']}.md").write_text(f"[Error] {e}")
    write_manifest(manifest)

def execute_agent_tool(tool_input: dict) -> str:
    agent_id = make_agent_id()
    output_file = AGENT_STORE / f"{agent_id}.md"
    manifest = {"agent_id": agent_id, "description": tool_input.get("description", ""), "status": "running",
                 "created_at": iso8601_now(), "output_file": str(output_file), "started_at": iso8601_now()}
    write_manifest(manifest)
    allowed = SUBAGENT_ALLOWED_TOOLS.get(tool_input.get("subagent_type", "general"), SUBAGENT_ALLOWED_TOOLS["general"])
    threading.Thread(target=run_agent_job, args=(manifest, tool_input.get("prompt", ""), allowed), daemon=True).start()
    return json.dumps(manifest)

# ── 错误恢复 ─────────────────────────────────────────────────
class FailureScenario(Enum):
    FILE_NOT_FOUND = "file_not_found"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    SANDBOX_BLOCKED = "sandbox_blocked"
    FETCH_ERROR = "fetch_error"
    UNKNOWN_TOOL = "unknown_tool"
    GENERIC_ERROR = "generic_error"

class RecoveryAction(Enum):
    RETRY = "retry"
    ESCALATE = "escalate"
    IGNORE = "ignore"

@dataclass
class RecoveryRecipe:
    action: RecoveryAction
    message_to_model: str
    max_attempts: int

RECOVERY_RECIPES = {
    FailureScenario.FILE_NOT_FOUND:    RecoveryRecipe(RecoveryAction.IGNORE,   "", 0),
    FailureScenario.PERMISSION_DENIED: RecoveryRecipe(RecoveryAction.ESCALATE, "", 1),
    FailureScenario.TIMEOUT:           RecoveryRecipe(RecoveryAction.RETRY,    "命令超时，已自动重试", MAX_RECOVERY_ATTEMPTS),
    FailureScenario.SANDBOX_BLOCKED:   RecoveryRecipe(RecoveryAction.IGNORE,   "", 0),
    FailureScenario.FETCH_ERROR:       RecoveryRecipe(RecoveryAction.IGNORE,   "", 0),
    FailureScenario.UNKNOWN_TOOL:      RecoveryRecipe(RecoveryAction.IGNORE,   "", 0),
    FailureScenario.GENERIC_ERROR:     RecoveryRecipe(RecoveryAction.RETRY,    "工具执行出错，已自动重试", MAX_RECOVERY_ATTEMPTS),
}

attempt_counter: dict[str, int] = {}
recovery_log: list[dict] = []

def is_failure(output: str) -> bool:
    return any(s in output for s in ["读取失败:", "写入失败:", "命令超时", "未知工具:", "[MCP Error]", "old_string 在文件中不存在", "[Sandbox]", "[FetchError]"])

def classify_failure(output: str) -> FailureScenario:
    if "命令超时" in output:  return FailureScenario.TIMEOUT
    if "未知工具:" in output:  return FailureScenario.UNKNOWN_TOOL
    if "[Sandbox]" in output:  return FailureScenario.SANDBOX_BLOCKED
    if "[FetchError]" in output: return FailureScenario.FETCH_ERROR
    if "No such file" in output or "不存在" in output: return FailureScenario.FILE_NOT_FOUND
    if "Permission denied" in output: return FailureScenario.PERMISSION_DENIED
    return FailureScenario.GENERIC_ERROR

def execute_tool_with_recovery(name: str, tool_input: dict, tool_use_id: str) -> str:
    output = execute_tool(name, tool_input)
    if not is_failure(output):
        return output
    scenario = classify_failure(output)
    recipe = RECOVERY_RECIPES[scenario]
    count = attempt_counter.get(tool_use_id, 0)
    recovery_log.append({"tool": name, "scenario": scenario.value, "action": recipe.action.value, "attempt": count + 1, "output_snippet": output[:80]})
    if recipe.action == RecoveryAction.RETRY and count < recipe.max_attempts:
        attempt_counter[tool_use_id] = count + 1
        retry_output = execute_tool(name, tool_input)
        return f"[Recovery] {recipe.message_to_model}\n{retry_output}" if not is_failure(retry_output) else retry_output
    if recipe.action == RecoveryAction.ESCALATE:
        print(f"\n  {S.ERR}✖ Recovery{S.RESET} 工具 '{name}' 失败：{S.DIM}{output}{S.RESET}")
        answer = input(f"  {S.WARN}请问如何处理？（输入建议或直接回车跳过）:{S.RESET} ").strip()
        if answer:
            return f"[Recovery] 用户建议：{answer}\n原始错误：{output}"
    return output

# ── 上下文压缩 ────────────────────────────────────────────────
COMPACT_THRESHOLD = CONFIG["compact"]["threshold_tokens"]
PRESERVE_RECENT = CONFIG["compact"]["preserve_recent_messages"]

def estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        if isinstance(m.get("content"), str):
            total += len(m["content"]) // 4
        elif isinstance(m.get("content"), list):
            for block in m["content"]:
                if isinstance(block, dict):
                    total += len(json.dumps(block)) // 4
    return total

def compact_messages(messages: list[dict]) -> list[dict]:
    if len(messages) <= PRESERVE_RECENT:
        return messages
    to_summarize = messages[:-PRESERVE_RECENT]
    recent = messages[-PRESERVE_RECENT:]
    summary_text = "\n".join(m["content"] if isinstance(m["content"], str) else json.dumps(m["content"]) for m in to_summarize)
    summary = client.messages.create(model=model, max_tokens=1000,
                                     messages=[{"role": "user", "content": f"请用 3-5 句话摘要以下对话：\n{summary_text}"}]).content[0].text
    return [{"role": "user", "content": f"[之前对话摘要] {summary}"}] + recent

# ── Slash 命令 ────────────────────────────────────────────────
total_input_tokens = 0
total_output_tokens = 0

def handle_slash_command(cmd: str, messages: list[dict]) -> tuple[bool, str]:
    cmd = cmd.strip()
    if cmd == "/help":
        return True, ("可用命令：\n"
                      "  /help    — 显示帮助\n"
                      "  /status  — 显示当前配置\n"
                      "  /cost    — 显示 token 用量\n"
                      "  /web     — 显示 web 工具统计\n"
                      "  /clear   — 清空对话历史\n"
                      "  /quit    — 退出")
    if cmd == "/status":
        return True, f"模型：{model}"
    if cmd == "/cost":
        return True, f"本会话 token — 输入：{total_input_tokens}  输出：{total_output_tokens}"
    if cmd == "/web":
        kb = web_stats["fetch_bytes"] / 1024
        return True, f"web_fetch：{web_stats['fetch_count']} 次，{kb:.1f} KB\nweb_search：{web_stats['search_count']} 次"
    if cmd == "/clear":
        messages.clear()
        return True, "对话历史已清空"
    if cmd in ("/quit", "/exit"):
        print("再见！")
        sys.exit(0)
    return False, ""

# ── Spinner ───────────────────────────────────────────────────
def make_spinner():
    stop_event = threading.Event()
    def spin():
        for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if stop_event.is_set():
                break
            print(f"\r{S.CYAN}{ch}{S.RESET} {S.DIM}thinking...{S.RESET}", end="", flush=True)
            time.sleep(0.1)
        print("\r" + " " * 30 + "\r", end="", flush=True)
    t = threading.Thread(target=spin, daemon=True)
    return t, stop_event

# ── Agentic loop ──────────────────────────────────────────────
def run_loop(messages: list[dict], system_prompt: str) -> None:
    global total_input_tokens, total_output_tokens
    iterations = 0
    while True:
        iterations += 1
        if iterations > MAX_ITERATIONS:
            print(f"\n[Loop] 已达最大迭代次数 {MAX_ITERATIONS}，停止。")
            return
        if estimate_tokens(messages) > COMPACT_THRESHOLD:
            messages[:] = compact_messages(messages)

        spinner_t, stop_evt = make_spinner()
        spinner_t.start()
        try:
            response = client.messages.create(model=model, max_tokens=max_tokens,
                                              system=system_prompt, messages=messages, tools=TOOLS)
        finally:
            stop_evt.set()
            spinner_t.join()

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if hasattr(block, "type") and block.type == "thinking":
                text = getattr(block, "thinking", "")
                if text:
                    for line in text.split("\n"):
                        print(f"  {S.THINK}{line}{S.RESET}")
            elif hasattr(block, "type") and block.type == "text":
                print(f"\n{block.text}")

        if response.stop_reason == "end_turn":
            return

        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if hasattr(b, "type") and b.type == "tool_use"]
            tool_results = []
            for b in tool_uses:
                summary = _fmt_tool_input(b.name, b.input)
                print(f"\n  {S.YELLOW}⏳{S.RESET} {S.TOOL}{b.name}{S.RESET} {S.TOOL_DIM}{summary}{S.RESET}")
                result = execute_tool_with_recovery(b.name, b.input, b.id)
                dot = f"{S.ERR}✖{S.RESET}" if is_failure(result) else f"{S.SUCCESS}●{S.RESET}"
                print(f"\033[1A\033[2K  {dot} {S.TOOL}{b.name}{S.RESET} {S.TOOL_DIM}{summary}{S.RESET}")
                for line in _fmt_result_summary(result).split("\n"):
                    print(f"    {S.GRAY}{line}{S.RESET}")
                tool_results.append({"type": "tool_result", "tool_use_id": b.id, "content": result})
            messages.append({"role": "user", "content": tool_results})

# ── Banner ────────────────────────────────────────────────────
_O = "\033[38;5;208m"
_ICON = [
    f"{_O} ▗████▖ {S.RESET}",
    f"{_O} █{S.RESET}◉  ◉{_O}█ {S.RESET}",
    f"{_O} ▝{S.RESET}△{_O}██{S.RESET}△{_O}▘ {S.RESET}",
    f"{_O}  ████  {S.RESET}",
]
_PAD = "        "

def _print_banner(version: str, feature_label: str, feature_value: str) -> None:
    """
    version:       里程碑编号，如 "m24"
    feature_label: 本里程碑核心功能标签，如 "todo"
    feature_value: 对应的工具名或说明，如 "TodoWrite / TaskCreate"
    """
    _cwd = str(WORKSPACE_ROOT).replace(str(Path.home()), "~")
    _tools_str = ", ".join(t["name"] for t in BUILTIN_TOOLS) + ", agent"

    def _row(icon_line, key, value):
        print(f"{icon_line}  {S.DIM}{key:<12}{S.RESET} {value}")

    _row(_ICON[0], "version",      version)
    _row(_ICON[1], "model",        f"{S.BOLD}{model}{S.RESET}")
    _row(_ICON[2], "tools",        _tools_str)
    _row(_ICON[3], feature_label,  f"{S.PLAN}{feature_value}{S.RESET}")
    print(f"{_PAD}  {S.DIM}{'cwd':<12}{S.RESET} {_cwd}")
    print(f"{_PAD}  {S.DIM}{'sandbox':<12}{S.RESET} {'on' if SANDBOX_ENABLED else 'off'}")

def _prompt(mode_indicator: str = "") -> str:
    """
    mode_indicator: 可选模式标记，如 f" {S.PLAN}[PLAN]{S.RESET}"
    """
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 60
    hr = f"{S.DIM}{'─' * cols}{S.RESET}"
    print()
    print(hr)
    print()
    print(hr)
    sys.stdout.write("\033[2A\r")
    sys.stdout.flush()
    user_in = input(f"{S.PROMPT}  ❯{S.RESET}{mode_indicator} ")
    print()
    return user_in.strip()


# ──────────────────────────────────────────────────────────────
# 以下是里程碑专属部分，新里程碑在这里添加功能
# ──────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    fan_md = load_fan_md()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    base = f"你是 fancode，一个帮助用户完成编程任务的 AI agent。当前时间：{now}。\n"
    base += f"沙盒路径边界：{'已启用' if SANDBOX_ENABLED else '已禁用'}，工作区：{WORKSPACE_ROOT}\n"
    if fan_md:
        base += f"\n---\n{fan_md}"
    return base

def main() -> None:
    system_prompt = build_system_prompt()
    _print_banner(version="mXX", feature_label="new feature", feature_value="tool_name")
    messages: list[dict] = []
    while True:
        try:
            user_input = _prompt()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{S.DIM}goodbye{S.RESET}")
            break
        if not user_input:
            continue
        if user_input.startswith("/"):
            handled, reply = handle_slash_command(user_input, messages)
            if handled:
                print(f"{S.DIM}{reply}{S.RESET}")
                continue
        messages.append({"role": "user", "content": user_input})
        run_loop(messages, system_prompt)

if __name__ == "__main__":
    main()
