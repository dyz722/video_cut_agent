"""Provider adapters for the main LLM.

The rest of the harness speaks Anthropic Messages-style blocks. Provider
adapters normalize mainstream APIs into that shape so the agent loop stays
unchanged.
"""

from dataclasses import dataclass
import json
import os

import requests


@dataclass
class TextBlock:
    type: str
    text: str


@dataclass
class ToolUseBlock:
    type: str
    id: str
    name: str
    input: dict


@dataclass
class MessageResponse:
    content: list
    stop_reason: str


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _block_type(block):
    return _field(block, "type")


def _tool_input(block):
    value = _field(block, "input", {})
    return value if isinstance(value, dict) else {}


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            typ = _block_type(block)
            if typ == "text":
                parts.append(str(_field(block, "text", "")))
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def anthropic_tools_to_openai(tools: list | None) -> list | None:
    if not tools:
        return None
    converted = []
    for tool in tools:
        converted.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return converted


def anthropic_messages_to_openai(messages: list, system: str | None = None) -> list:
    converted = []
    if system:
        converted.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "assistant" and isinstance(content, list):
            text_parts = []
            tool_calls = []
            for block in content:
                typ = _block_type(block)
                if typ == "text":
                    text_parts.append(str(_field(block, "text", "")))
                elif typ == "tool_use":
                    tool_calls.append({
                        "id": _field(block, "id"),
                        "type": "function",
                        "function": {
                            "name": _field(block, "name"),
                            "arguments": json.dumps(_tool_input(block), ensure_ascii=False),
                        },
                    })
            out = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                out["tool_calls"] = tool_calls
            converted.append(out)
            continue

        if role == "user" and isinstance(content, list):
            user_text = []
            for part in content:
                typ = _block_type(part)
                if isinstance(part, dict) and typ == "tool_result":
                    converted.append({
                        "role": "tool",
                        "tool_call_id": part["tool_use_id"],
                        "content": str(part.get("content", "")),
                    })
                elif typ == "text":
                    user_text.append(str(_field(part, "text", "")))
                else:
                    user_text.append(str(part))
            if user_text:
                converted.append({"role": "user", "content": "\n".join(user_text)})
            continue

        converted.append({"role": role, "content": _content_text(content)})
    return converted


def _parse_arguments(raw: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        return {"_raw_arguments": raw}


class OpenAICompatMessages:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key

    @property
    def chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def create(self, model: str, messages: list, system: str | None = None,
               tools: list | None = None, max_tokens: int = 8000):
        payload = {
            "model": model,
            "messages": anthropic_messages_to_openai(messages, system),
            "max_tokens": max_tokens,
        }
        converted_tools = anthropic_tools_to_openai(tools)
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = "auto"
        timeout = float(os.getenv("OPENAI_TIMEOUT", "120"))
        response = requests.post(
            self.chat_completions_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI-compatible API error {response.status_code}: "
                               f"{response.text[:1200]}")
        data = response.json()
        message = data["choices"][0]["message"]
        blocks = []
        if message.get("content"):
            blocks.append(TextBlock(type="text", text=message["content"]))
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            blocks.append(ToolUseBlock(
                type="tool_use",
                id=call.get("id", ""),
                name=fn.get("name", ""),
                input=_parse_arguments(fn.get("arguments", "")),
            ))
        return MessageResponse(content=blocks or [TextBlock(type="text", text="")],
                               stop_reason="tool_use" if message.get("tool_calls") else "end_turn")


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str):
        self.messages = OpenAICompatMessages(base_url, api_key)
