"""World + tools for the stasis experiment.

Every tool either changes the world or does not. That is the only signal the
detector is allowed to use: the world, not the model's self-report.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


def fingerprint(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


@dataclass
class World:
    docs: dict[str, str] = field(default_factory=dict)
    summaries: dict[str, str] = field(default_factory=dict)
    tickets: dict[str, str] = field(default_factory=dict)
    answers: list[str] = field(default_factory=list)
    kb: list[tuple[str, str]] = field(default_factory=list)

    def state(self) -> dict:
        """What counts as progress. Counters and timestamps are excluded."""
        return {
            "summaries": self.summaries,
            "tickets": self.tickets,
            "answers": self.answers,
        }


class Tools:
    def __init__(self, w: World) -> None:
        self.w = w

    def read_doc(self, doc_id: str) -> str:
        return self.w.docs.get(doc_id, "")

    def write_summary(self, doc_id: str, text: str) -> str:
        self.w.summaries[doc_id] = text
        return "ok"

    def get_ticket(self, tid: str) -> str:
        return self.w.tickets.get(tid, "unknown")

    def set_ticket(self, tid: str, status: str) -> str:
        self.w.tickets[tid] = status
        return "ok"

    def search(self, query: str, k: int = 2) -> list[str]:
        q = query.lower().split()
        scored = sorted(self.w.kb, key=lambda kv: -sum(t in kv[1].lower() for t in q))
        return [t for t, _ in scored[:k]]

    def append_answer(self, text: str) -> str:
        self.w.answers.append(text)
        return "ok"

    def call(self, name: str, args: dict) -> Any:
        return getattr(self, name)(**args)


def make_world(n_docs: int = 6, n_tickets: int = 6, n_kb: int = 8) -> World:
    w = World(
        docs={f"d{i}": f"Document {i} about topic {i}" for i in range(n_docs)},
        tickets={f"T{i}": "open" for i in range(n_tickets)},
        kb=[(f"k{i}", f"passage {i} about {'gpu' if i % 2 else 'memory'} pricing") for i in range(n_kb)],
    )
    return w
