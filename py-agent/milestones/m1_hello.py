# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic", "python-dotenv"]
# ///
#
# M1: 能对话的最小 agent（streaming）
#
# 这是整个课程的起点：用最少的代码让模型说话。
# 核心：client.messages.stream() 流式接收 token，逐字打印。
#
# 验收：cd py-agent && uv run milestones/m1_hello.py

import os
from pathlib import Path

from dotenv import load_dotenv
import anthropic

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    base_url=os.environ.get("ANTHROPIC_BASE_URL"),
)
model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

messages = [{"role": "user", "content": "hi，用一句话介绍你自己"}]

with client.messages.stream(max_tokens=1024, messages=messages, model=model) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
print()
