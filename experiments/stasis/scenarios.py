"""Six loop shapes: three healthy, three that stall. Ground truth is known."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class Scenario:
    name: str
    actions: list[tuple[str, dict]]
    onset: int | None  # step where progress stops; None for healthy
    max_steps: int = 24


def _shuffle(seed: int, items: list) -> list:
    out = list(items)
    random.Random(seed).shuffle(out)
    return out


def healthy_batch(seed: int) -> Scenario:
    docs = _shuffle(seed, [f"d{i}" for i in range(6)])
    acts = []
    for d in docs:
        acts.append(("read_doc", {"doc_id": d}))
        acts.append(("write_summary", {"doc_id": d, "text": f"Summary of {d}"}))
    return Scenario("batch_docs", acts, onset=None)


def stall_batch(seed: int) -> Scenario:
    docs = _shuffle(seed, [f"d{i}" for i in range(6)])
    acts = []
    for d in docs[:3]:
        acts.append(("read_doc", {"doc_id": d}))
        acts.append(("write_summary", {"doc_id": d, "text": f"Summary of {d}"}))
    onset = len(acts)
    for _ in range(18):
        acts.append(("read_doc", {"doc_id": docs[0]}))
        acts.append(("write_summary", {"doc_id": docs[0], "text": f"Summary of {docs[0]}"}))
    return Scenario("batch_docs", acts, onset=onset)


def healthy_tickets(seed: int) -> Scenario:
    tids = _shuffle(seed, [f"T{i}" for i in range(6)])
    acts = []
    for t in tids:
        acts.append(("get_ticket", {"tid": t}))
        acts.append(("set_ticket", {"tid": t, "status": "in_progress"}))
        acts.append(("set_ticket", {"tid": t, "status": "resolved"}))
    return Scenario("tickets", acts, onset=None)


def stall_tickets(seed: int) -> Scenario:
    tids = _shuffle(seed, [f"T{i}" for i in range(6)])
    acts = []
    for t in tids[:2]:
        acts.append(("get_ticket", {"tid": t}))
        acts.append(("set_ticket", {"tid": t, "status": "resolved"}))
    onset = len(acts)
    for _ in range(20):
        acts.append(("get_ticket", {"tid": tids[0]}))
    return Scenario("tickets", acts, onset=onset)


def healthy_rag(seed: int) -> Scenario:
    qs = _shuffle(seed, [f"passage about gpu {i}" for i in range(6)])
    acts = []
    for q in qs:
        acts.append(("search", {"query": q}))
        acts.append(("append_answer", {"text": f"answer for {q}"}))
    return Scenario("rag", acts, onset=None)


def stall_rag(seed: int) -> Scenario:
    qs = _shuffle(seed, [f"passage about gpu {i}" for i in range(6)])
    acts = []
    for q in qs[:2]:
        acts.append(("search", {"query": q}))
        acts.append(("append_answer", {"text": f"answer for {q}"}))
    onset = len(acts)
    for _ in range(20):
        acts.append(("search", {"query": qs[0]}))
    return Scenario("rag", acts, onset=onset)


def healthy_think(seed: int) -> Scenario:
    """Legitimate read-heavy phase: six reads, then writes. No state change for 6 steps."""
    docs = _shuffle(seed, [f"d{i}" for i in range(6)])
    acts = [("read_doc", {"doc_id": d}) for d in docs]
    acts += [("write_summary", {"doc_id": d, "text": f"Summary of {d}"}) for d in docs]
    return Scenario("think_then_write", acts, onset=None)


def stall_rephrase(seed: int) -> Scenario:
    """Churn: the world changes every step, but the task does not advance."""
    docs = _shuffle(seed, [f"d{i}" for i in range(6)])
    acts = [("read_doc", {"doc_id": docs[0]}), ("write_summary", {"doc_id": docs[0], "text": "v0"})]
    onset = len(acts)
    for v in range(1, 21):
        # same document, cosmetic edits: state changes, progress does not
        acts.append(("write_summary", {"doc_id": docs[0], "text": f"v{v}"}))
    return Scenario("rephrase_churn", acts, onset=onset)


BUILDERS = {
    ("batch_docs", "healthy"): healthy_batch,
    ("batch_docs", "stall"): stall_batch,
    ("tickets", "healthy"): healthy_tickets,
    ("tickets", "stall"): stall_tickets,
    ("rag", "healthy"): healthy_rag,
    ("rag", "stall"): stall_rag,
    ("read_heavy", "healthy"): healthy_think,
    ("rephrase", "stall"): stall_rephrase,
}
