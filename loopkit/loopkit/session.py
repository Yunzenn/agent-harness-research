"""Append-only session log: the single source of truth a run can be replayed from."""

from __future__ import annotations

import json
from typing import Any, Optional

from .types import Event, ToolCall


class SessionLog:
    def __init__(self) -> None:
        self._events: list[Event] = []

    # -- write ------------------------------------------------------------
    def append(self, kind: str, **data: Any) -> Event:
        ev = Event(seq=len(self._events), kind=kind, data=data)
        self._events.append(ev)
        return ev

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    # -- read -------------------------------------------------------------
    def messages(self) -> list[dict[str, Any]]:
        """Projection fed to the model. Log-only events never appear here."""
        out: list[dict[str, Any]] = []
        for ev in self._events:
            k, d = ev.kind, ev.data
            if k == "user":
                out.append({"role": "user", "content": d["text"]})
            elif k == "assistant_text":
                out.append({"role": "assistant", "content": d["text"]})
            elif k == "assistant_calls":
                out.append(
                    {
                        "role": "assistant",
                        "content": d.get("text") or "",
                        "tool_calls": [
                            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args_json"]}}
                            for c in d["calls"]
                        ],
                    }
                )
            elif k == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": d["call_id"],
                        "name": d["name"],
                        "content": d["content"],
                    }
                )
        return out

    def tool_calls(self) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for ev in self._events:
            if ev.kind == "assistant_calls":
                for c in ev.data["calls"]:
                    calls.append(ToolCall(name=c["name"], args=c["args"], id=c["id"]))
        return calls

    def transcript(self) -> str:
        lines = []
        for ev in self._events:
            k, d = ev.kind, ev.data
            if k in ("user", "assistant_text"):
                lines.append(f"[{k}] {d.get('text', '')}")
            elif k == "assistant_calls":
                for c in d["calls"]:
                    lines.append(f"[call] {c['name']}({json.dumps(c['args'], ensure_ascii=False)})")
            elif k == "tool_result":
                flag = "" if d.get("ok") else f" ({d.get('code')})"
                body = d["content"]
                body = body if len(body) <= 300 else body[:300] + " …"
                lines.append(f"[result:{d['name']}{flag}] {body}")
            elif k in ("note", "gate"):
                lines.append(f"[{k}] {d.get('text', d)}")
        return "\n".join(lines)

    # -- durability -------------------------------------------------------
    def checkpoint(self) -> dict[str, Any]:
        return {"version": 1, "events": [{"seq": e.seq, "kind": e.kind, "data": e.data} for e in self._events]}

    @classmethod
    def restore(cls, snap: dict[str, Any]) -> "SessionLog":
        log = cls()
        for raw in snap["events"]:
            log._events.append(Event(seq=raw["seq"], kind=raw["kind"], data=raw["data"]))
        return log

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.checkpoint(), fh, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "SessionLog":
        with open(path, encoding="utf-8") as fh:
            return cls.restore(json.load(fh))

    def __len__(self) -> int:
        return len(self._events)

    def last_text(self) -> str:
        for ev in reversed(self._events):
            if ev.kind in ("assistant_text", "user"):
                return ev.data.get("text", "")
        return ""

    def find(self, kind: str) -> list[Event]:
        return [e for e in self._events if e.kind == kind]
