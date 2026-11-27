"""Real-model version of the stasis experiment.

Runs a model (any OpenAI-compatible endpoint) on three tool tasks, records every
step, then labels each trace by *task-level* progress:

  completed      : the task state reached the goal
  stalled_frozen : no progress and no new (world, result) signature
  stalled_churn  : no progress but signatures keep changing (busy, useless)

Local models often emit the tool call as JSON *text* instead of the native
`tool_calls` field; both protocols are accepted (`parse_action`).

    python model_run.py --base-url http://127.0.0.1:11434/v1 --model qwen2.5-coder:7b --tasks 9
    python model_run.py --mock
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from experiments.stasis.monitor import StasisMonitor  # noqa: E402
from experiments.stasis.world import Tools, fingerprint, make_world  # noqa: E402

TOOL_SPECS = [
    ("read_doc", "Read one document by id.", {"doc_id": {"type": "string"}}, ["doc_id"]),
    ("write_summary", "Save a one-line summary for a document.", {"doc_id": {"type": "string"}, "text": {"type": "string"}}, ["doc_id", "text"]),
    ("get_ticket", "Read a ticket.", {"tid": {"type": "string"}}, ["tid"]),
    ("set_ticket", "Set a ticket status: open | in_progress | resolved.", {"tid": {"type": "string"}, "status": {"type": "string"}}, ["tid", "status"]),
    ("search", "Search the knowledge base.", {"query": {"type": "string"}}, ["query"]),
    ("append_answer", "Record a final answer.", {"text": {"type": "string"}}, ["text"]),
]

TOOLS = [
    {"type": "function", "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": p, "required": r}}}
    for n, d, p, r in TOOL_SPECS
]

TOOL_DOC = "\n".join(f'{n} :: {json.dumps(p)}' for n, d, p, r in TOOL_SPECS)

PROTOCOL_HINT = (
    "Available tools (name :: arguments):\n"
    + TOOL_DOC
    + "\n\nProtocol: reply with ONE JSON object only, no prose, no code fences:\n"
    '{"name": "<tool>", "arguments": {...}}\n'
    "When the task is complete, reply with exactly: DONE"
)

TASKS = {
    "batch": {
        "system": "You summarise documents. " + PROTOCOL_HINT,
        "user": "Summarise all documents d0..d5. For each: read_doc, then write_summary. Then reply DONE.",
        "done": lambda w: len(w.summaries) >= 6,
        "progress": lambda w: len(w.summaries) / 6,
    },
    "tickets": {
        "system": "You resolve support tickets. " + PROTOCOL_HINT,
        "user": "Resolve tickets T0..T5: get_ticket, then set_ticket in_progress, then resolved. Then reply DONE.",
        "done": lambda w: all(v == "resolved" for v in w.tickets.values()),
        "progress": lambda w: sum(v == "resolved" for v in w.tickets.values()) / 6,
    },
    "rag": {
        "system": "You answer questions from a knowledge base. " + PROTOCOL_HINT,
        "user": "Answer six questions about gpu and memory pricing: search, then append_answer. Then reply DONE.",
        "done": lambda w: len(w.answers) >= 6,
        "progress": lambda w: len(w.answers) / 6,
    },
}


def chat(base_url: str, model: str, messages: list, tools: list, temperature: float) -> dict:
    payload = json.dumps({"model": model, "messages": messages, "temperature": temperature}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def parse_action(msg: dict) -> tuple[list[dict], str]:
    """Return (calls, protocol).

    Local models are inconsistent: some emit native tool_calls, some a single JSON
    object, some several concatenated objects, some an array, some fenced. Accept
    all of them, and never let a formatting quirk look like a stalled agent.
    """
    native = msg.get("tool_calls") or []
    if native:
        return native, "native"
    content = (msg.get("content") or "").strip()
    if content.upper().startswith("DONE"):
        return [], "text"

    body = re.sub(r"```(?:json)?", "", content).strip()
    decoder = json.JSONDecoder()
    objs: list = []
    idx = 0
    while idx < len(body) and len(objs) < 4:
        while idx < len(body) and body[idx] not in "[{":
            idx += 1
        if idx >= len(body):
            break
        try:
            obj, end = decoder.raw_decode(body, idx)
        except json.JSONDecodeError:
            idx += 1
            continue
        objs.append(obj)
        idx = end

    calls: list[dict] = []
    for obj in objs:
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                args = item.get("arguments") or item.get("args") or item.get("parameters") or {}
                calls.append(
                    {
                        "id": f"c{len(calls)}",
                        "type": "function",
                        "function": {"name": item["name"], "arguments": json.dumps(args)},
                    }
                )
    return calls, "text"


def run_task(kind: str, base_url: str | None, model: str, temperature: float, max_steps: int, seed: int, mock: bool) -> dict:
    spec = TASKS[kind]
    w = make_world()
    tools = Tools(w)
    messages = [{"role": "system", "content": spec["system"]}, {"role": "user", "content": spec["user"]}]
    trace = []
    mon = StasisMonitor(k=4)

    for step in range(max_steps):
        if mock:
            msg = _mock_message(kind, w, step, seed)
            protocol = "mock"
        else:
            data = chat(base_url, model, messages, TOOLS, temperature)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            protocol = ""
        calls, proto = parse_action(msg)
        protocol = protocol or proto
        if not calls:
            break

        for call in calls[:2]:
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments")
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
            try:
                result = tools.call(name, args)
            except Exception as exc:  # noqa: BLE001
                result = f"error: {exc}"
            mon.observe(step, w.state(), result)
            trace.append({"step": step, "tool": name, "args": args, "result": str(result)[:160], "world": fingerprint(w.state()), "progress": round(spec["progress"](w), 3)})

            if protocol == "native":
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": str(result)[:800]})
            else:
                messages.append({"role": "assistant", "content": json.dumps({"name": name, "arguments": args})})
                messages.append({"role": "user", "content": f"Result of {name}: {str(result)[:800]}"})

    return {
        "kind": kind,
        "seed": seed,
        "steps": len({t["step"] for t in trace}),
        "calls": len(trace),
        "done": spec["done"](w),
        "progress": round(spec["progress"](w), 3),
        "distinct_signatures": len(mon.seen),
        "detected_at": mon.detected_at,
        "trace": trace,
    }


def _mock_message(kind: str, w, step: int, seed: int) -> dict:
    if kind == "batch":
        pending = [d for d in w.docs if d not in w.summaries]
        if not pending:
            return {"content": "DONE"}
        d = pending[0]
        return {"content": json.dumps({"name": "write_summary", "arguments": {"doc_id": d, "text": f"summary {d}"}})}
    if kind == "tickets":
        pending = [t for t, v in w.tickets.items() if v != "resolved"]
        if not pending:
            return {"content": "DONE"}
        t = pending[0]
        nxt = "in_progress" if w.tickets[t] == "open" else "resolved"
        return {"content": json.dumps({"name": "set_ticket", "arguments": {"tid": t, "status": nxt}})}
    if len(w.answers) >= 6:
        return {"content": "DONE"}
    return {"content": json.dumps({"name": "append_answer", "arguments": {"text": f"answer {len(w.answers)}"}})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--per-task", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_traces.json"))
    args = ap.parse_args()

    rows = []
    t0 = time.time()
    for kind in TASKS:
        for seed in range(args.per_task):
            rows.append(run_task(kind, args.base_url, args.model, args.temperature, args.max_steps, seed, args.mock))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)

    done = sum(1 for r in rows if r["done"])
    stalled = [r for r in rows if not r["done"]]
    print(f"tasks={len(rows)} completed={done} stalled={len(stalled)} in {time.time()-t0:.1f}s")
    for r in rows:
        print(f"  {r['kind']:8s} seed={r['seed']} steps={r['steps']:2d} calls={r['calls']:2d} progress={r['progress']:.2f} done={r['done']} sig={r['distinct_signatures']}")
    if stalled:
        frozen = sum(1 for r in stalled if r["distinct_signatures"] <= 3)
        print(f"  stalled: frozen-ish={frozen} churn-ish={len(stalled)-frozen}")
    print("wrote", args.out)


if __name__ == "__main__":
    main()

