"""Thin OpenAI-compatible chat client (defaults to Zhipu GLM). Key comes from env only."""
from __future__ import annotations

import json
import os
import re

import asyncio

import httpx


class LLMError(RuntimeError):
    """The model provider refused or failed; message is safe to show in chat."""

BASE = os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "glm-4.6")
_client = httpx.AsyncClient(timeout=180)


async def chat(messages: list[dict], *, temperature: float = 0.2, max_tokens: int = 2048,
               tools: list | None = None, json_mode: bool = False) -> dict:
    body: dict = {"model": MODEL, "messages": messages, "temperature": temperature,
                  "max_tokens": max_tokens}
    if "bigmodel" in BASE:  # GLM: skip slow reasoning for chat-speed replies
        body["thinking"] = {"type": "disabled"}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(4):
        try:
            r = await _client.post(f"{BASE}/chat/completions", json=body,
                                   headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"})
        except httpx.HTTPError as e:
            if attempt == 3:
                raise LLMError(f"model provider unreachable ({type(e).__name__})") from e
            await asyncio.sleep(2 ** attempt); continue
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]
        quota = '"1310"' in r.text or '"1113"' in r.text  # plan quota / balance: retrying won't help
        if quota or r.status_code < 429 or attempt == 3:
            raise LLMError(f"model provider returned HTTP {r.status_code}" + (" (quota reached)" if quota else ""))
        await asyncio.sleep(2 ** attempt)
    raise LLMError("model provider failed")


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


async def chat_json(messages: list[dict], **kw) -> dict:
    msg = await chat(messages, json_mode=True, **kw)
    return parse_json(msg.get("content", ""))
