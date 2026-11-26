"""Providers: the only place a "model" appears.

`MockProvider` makes the whole library testable offline and lets the benchmark
measure wall-clock honestly (it sleeps a configurable amount, like a real call).
`OpenAICompatProvider` talks to any /chat/completions endpoint.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from .types import Action, LLMRequest, LLMResponse, ToolCall


class Provider(Protocol):
    name: str

    def complete(self, req: LLMRequest) -> LLMResponse: ...


class MockProvider:
    """Deterministic stand-in for a model.

    `brain(req) -> Action` decides what the "model" does. `latency_s` simulates
    network + decode time; `chars_per_token` converts prompt size into usage.
    """

    def __init__(
        self,
        brain: Callable[[LLMRequest], Action],
        latency_s: float = 0.0,
        chars_per_token: float = 4.0,
        name: str = "mock",
    ) -> None:
        self.brain = brain
        self.latency_s = latency_s
        self.chars_per_token = chars_per_token
        self.name = name
        self.calls = 0
        self.sleep_s = 0.0

    def complete(self, req: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        if self.latency_s:
            time.sleep(self.latency_s)
            self.sleep_s += self.latency_s
        self.calls += 1
        action = self.brain(req)
        text = action.text
        out_chars = len(text or "") + len(json.dumps([c.args for c in action.calls], ensure_ascii=False))
        return LLMResponse(
            text=text,
            tool_calls=action.calls,
            tokens_in=int(req.approx_chars() / self.chars_per_token),
            tokens_out=int(out_chars / self.chars_per_token) + 1,
            latency_s=time.monotonic() - started,
            model=req.model,
        )


@dataclass
class Tiered:
    """Route requests to different providers by model name prefix.

    Cheap classifier/planner calls land on the small model, execution on the big
    one. The speedup is real (small models are faster) and so is the saving.
    """

    tiers: dict[str, Provider]

    @property
    def name(self) -> str:
        return "tiered:" + "+".join(sorted(self.tiers))

    def complete(self, req: LLMRequest) -> LLMResponse:
        for prefix, provider in self.tiers.items():
            if req.model.startswith(prefix):
                return provider.complete(req)
        if "default" in self.tiers:
            return self.tiers["default"].complete(req)
        # No match: the last tier is the fallback (by convention the strongest).
        return list(self.tiers.values())[-1].complete(req)


class OpenAICompatProvider:
    """Minimal /chat/completions client. No SDK, no dependencies.

    `transport` is injectable so the request/response shaping is unit-testable
    without network access.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        transport: Optional[Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]] = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.name = f"openai-compat:{model}"
        self._transport = transport or self._http

    def _http(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - explicit https endpoint
            return json.loads(resp.read().decode("utf-8"))

    def complete(self, req: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        payload: dict[str, Any] = {
            "model": req.model or self.model,
            "messages": ([{"role": "system", "content": req.system}] if req.system else []) + req.messages,
            "temperature": req.temperature,
        }
        if req.tools:
            payload["tools"] = req.tools
            payload["tool_choice"] = "auto"

        data = self._transport(
            f"{self.base_url}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            payload,
        )
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(name=fn.get("name", ""), args=args, id=raw.get("id", "")))
        usage = data.get("usage") or {}
        return LLMResponse(
            text=msg.get("content"),
            tool_calls=calls,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_s=time.monotonic() - started,
            model=data.get("model", self.model),
        )
