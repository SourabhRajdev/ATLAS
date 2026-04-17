"""ModelRouter — Multi-provider LLM with automatic failover.

Provider chain: Gemini → Groq → Ollama (local)
On transient errors (503, 429, timeout), the failing provider enters a 30s
cooldown and the next provider takes over. Transparent to Executor.

Unified message format (internal):
  {"role": "user"|"model", "parts": [
    {"text": "..."},
    {"function_call": {"name": "...", "args": {...}}},
    {"function_response": {"name": "...", "response": {"result": "..."}}},
  ]}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("atlas.model_router")

_TRANSIENT_KEYWORDS = (
    "503", "502", "429", "rate limit", "rate_limit",
    "timeout", "timed out", "connection", "unavailable",
    "overloaded", "temporarily", "resource_exhausted",
)


def _is_transient(err: str) -> bool:
    s = err.lower()
    return any(k in s for k in _TRANSIENT_KEYWORDS)


# ---------------------------------------------------------------------------
# Unified response types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    _id: str = ""   # provider-assigned call ID (used for OAI matching)


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""


# ---------------------------------------------------------------------------
# Message format converters (internal → OpenAI)
# ---------------------------------------------------------------------------

def _to_oai_messages(messages: list[dict], system_prompt: str) -> list[dict]:
    """Convert internal format to OpenAI-compatible messages list."""
    result: list[dict] = [{"role": "system", "content": system_prompt}]
    call_counter = 0
    last_call_ids: dict[str, str] = {}  # name → id, reset each assistant tool-call turn

    for msg in messages:
        role = msg["role"]
        parts = msg.get("parts", [])

        texts = [p["text"] for p in parts if "text" in p]
        fn_calls = [p["function_call"] for p in parts if "function_call" in p]
        fn_responses = [p["function_response"] for p in parts if "function_response" in p]

        if fn_responses:
            # Tool result messages — must reference the tool_call_id
            for fr in fn_responses:
                name = fr["name"]
                resp = fr.get("response", {})
                content = resp.get("result", "") if isinstance(resp, dict) else str(resp)
                tc_id = last_call_ids.get(name, f"call_{name}")
                result.append({"role": "tool", "tool_call_id": tc_id, "content": content})

        elif fn_calls:
            # Assistant turn with tool calls
            last_call_ids = {}
            oai_tcs = []
            for fc in fn_calls:
                name = fc["name"]
                args = fc.get("args", {})
                call_counter += 1
                tc_id = f"call_{name}_{call_counter}"
                last_call_ids[name] = tc_id
                oai_tcs.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                })
            result.append({
                "role": "assistant",
                "content": "\n".join(texts) or None,
                "tool_calls": oai_tcs,
            })

        else:
            oai_role = "assistant" if role == "model" else role
            result.append({"role": oai_role, "content": "\n".join(texts) or ""})

    return result


def _build_oai_tools(tool_defs: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool defs to OpenAI function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tool_defs
    ]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class GeminiProvider:
    name = "gemini"

    def __init__(self, client: Any, config: Any) -> None:
        self._client = client
        self._config = config

    def _build_tools(self, tool_defs: list[dict]) -> list:
        from google.genai import types
        TYPE_MAP = {
            "string": types.Type.STRING, "integer": types.Type.INTEGER,
            "number": types.Type.NUMBER, "boolean": types.Type.BOOLEAN,
            "array": types.Type.ARRAY, "object": types.Type.OBJECT,
        }
        declarations = []
        for t in tool_defs:
            schema = t.get("input_schema", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])
            gemini_props = {
                pname: types.Schema(
                    type=TYPE_MAP.get(pschema.get("type", "string").lower(), types.Type.STRING),
                    description=pschema.get("description", ""),
                )
                for pname, pschema in props.items()
            }
            declarations.append(types.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties=gemini_props,
                    required=required,
                ) if gemini_props else None,
            ))
        return [types.Tool(function_declarations=declarations)] if declarations else []

    def _call_sync(self, messages: list[dict], gemini_tools: list, system_prompt: str) -> Any:
        from google.genai import types
        contents = []
        for m in messages:
            parts = []
            for p in m.get("parts", []):
                if "text" in p:
                    parts.append(types.Part(text=p["text"]))
                elif "function_call" in p:
                    fc = p["function_call"]
                    parts.append(types.Part(function_call=types.FunctionCall(
                        name=fc["name"], args=fc.get("args", {}),
                    )))
                elif "function_response" in p:
                    fr = p["function_response"]
                    parts.append(types.Part(function_response=types.FunctionResponse(
                        name=fr["name"], response=fr.get("response", {}),
                    )))
            contents.append(types.Content(role=m["role"], parts=parts))

        return self._client.models.generate_content(
            model=self._config.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=gemini_tools,
                temperature=0.5,
                max_output_tokens=self._config.max_tokens,
            ),
        )

    async def generate(
        self, messages: list[dict], tool_defs: list[dict], system_prompt: str,
    ) -> LLMResponse:
        gemini_tools = self._build_tools(tool_defs)
        response = await asyncio.to_thread(self._call_sync, messages, gemini_tools, system_prompt)

        if not response or not response.candidates:
            return LLMResponse(provider=self.name)

        text = ""
        tool_calls: list[ToolCall] = []
        candidate = response.candidates[0]
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if getattr(part, "text", None):
                    text += part.text
                fc = getattr(part, "function_call", None)
                if fc and fc.name:
                    tool_calls.append(ToolCall(name=fc.name, args=dict(fc.args or {})))
        return LLMResponse(text=text, tool_calls=tool_calls, provider=self.name)


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from groq import AsyncGroq
            self._client = AsyncGroq(api_key=self._api_key)
        return self._client

    async def generate(
        self, messages: list[dict], tool_defs: list[dict], system_prompt: str,
    ) -> LLMResponse:
        client = self._get_client()
        oai_msgs = _to_oai_messages(messages, system_prompt)
        tools = _build_oai_tools(tool_defs) if tool_defs else None

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": oai_msgs,
            "temperature": 0.5,
            "max_tokens": 4096,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        text = choice.message.content or ""
        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                tool_calls.append(ToolCall(name=tc.function.name, args=args, _id=tc.id))
        return LLMResponse(text=text, tool_calls=tool_calls, provider=self.name)


class OllamaProvider:
    """Ollama local LLM via /api/chat (httpx, no extra deps)."""
    name = "ollama"

    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def generate(
        self, messages: list[dict], tool_defs: list[dict], system_prompt: str,
    ) -> LLMResponse:
        import httpx
        oai_msgs = _to_oai_messages(messages, system_prompt)
        tools = _build_oai_tools(tool_defs) if tool_defs else None

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": oai_msgs,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        msg = data.get("message", {})
        text = msg.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            name = fn.get("name", "")
            if name:
                tool_calls.append(ToolCall(name=name, args=args))
        return LLMResponse(text=text, tool_calls=tool_calls, provider=self.name)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class ModelRouter:
    """Tries providers in order, falls back on transient errors."""

    _COOLDOWN_S = 30

    def __init__(self, providers: list) -> None:
        self._providers = providers
        self._cooldown: dict[str, float] = {}

    def _available(self, p: Any) -> bool:
        return time.time() >= self._cooldown.get(p.name, 0)

    def _cool(self, p: Any) -> None:
        self._cooldown[p.name] = time.time() + self._COOLDOWN_S
        logger.warning("provider '%s' on cooldown for %ds", p.name, self._COOLDOWN_S)

    def active_provider(self) -> str:
        for p in self._providers:
            if self._available(p):
                return p.name
        return "none"

    async def generate(
        self,
        messages: list[dict],
        tool_defs: list[dict],
        system_prompt: str,
    ) -> LLMResponse:
        last_err: Exception | None = None
        for p in self._providers:
            if not self._available(p):
                logger.debug("skipping provider '%s' (cooldown)", p.name)
                continue
            try:
                result = await p.generate(messages, tool_defs, system_prompt)
                if p.name != "gemini":
                    logger.info("answered by fallback provider: %s", p.name)
                return result
            except Exception as e:
                last_err = e
                if _is_transient(str(e)):
                    logger.warning("provider '%s' transient error: %s — trying next", p.name, e)
                    self._cool(p)
                    continue
                raise  # non-transient — re-raise immediately

        raise RuntimeError(f"All LLM providers failed. Last: {last_err}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_model_router(config: Any) -> tuple["ModelRouter", Any]:
    """Build ModelRouter from config. Returns (router, gemini_client).

    gemini_client is returned separately so AutonomyLoop can keep using it.
    """
    from google import genai

    providers: list = []
    gemini_client = None

    if config.gemini_api_key:
        gemini_client = genai.Client(api_key=config.gemini_api_key)
        providers.append(GeminiProvider(gemini_client, config))

    if config.groq_api_key:
        providers.append(GroqProvider(api_key=config.groq_api_key, model=config.groq_model))

    # Ollama always added — fails fast if not running (caught as transient)
    providers.append(OllamaProvider(model=config.ollama_model, base_url=config.ollama_base_url))

    if not providers:
        raise RuntimeError("No LLM providers configured. Set ATLAS_GEMINI_API_KEY or ATLAS_GROQ_API_KEY.")

    router = ModelRouter(providers)
    logger.info(
        "ModelRouter ready: %s",
        " → ".join(p.name for p in providers),
    )
    return router, gemini_client
