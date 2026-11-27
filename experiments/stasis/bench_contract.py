"""A/B benchmark: same model, same tasks, with and without the contract.

baseline : the loop ends as soon as the model stops calling tools (it says DONE).
contract : a DONE is rejected while done_when() is false; on drift the contract first
           tells the model what "the next step" is, and only stops the run if the model
           still does not advance; forbidden calls are refused.

Measures per task and seed: correct / false_done / blocked / calls / progress.

    python experiments/stasis/bench_contract.py --per-task 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "loopkit"))

from experiments.stasis.model_run import TASKS, TOOLS, chat, parse_action  # noqa: E402
from experiments.stasis.world import Tools, make_world  # noqa: E402
from loopkit.contract import Contract, ContractMonitor  # noqa: E402


def _hint(kind: str, w) -> str:
    if kind == "tickets":
        pending = [t for t, v in w.tickets.items() if v != "resolved"]
        return f"Finish {pending[0]}: call set_ticket with status 'resolved' before moving on." if pending else ""
    if kind == "rag":
        return f"You have {len(w.answers)}/6 answers: call search, then append_answer for the next one."
    pending = [d for d in w.docs if d not in w.summaries]
    return f"Next document: {pending[0]}: read_doc then write_summary." if pending else ""


def contract_for(kind: str) -> Contract:
    spec = TASKS[kind]
    return Contract(
        name=kind,
        done_when=lambda w: spec["done"](w),
        advances=lambda w: spec["progress"](w),
        stall_steps=4,
        hint=lambda w: _hint(kind, w),
    )


def run(kind: str, seed: int, mode: str, base_url: str, model: str, max_steps: int) -> dict:
    spec = TASKS[kind]
    w = make_world()
    tools = Tools(w)
    messages = [{"role": "system", "content": spec["system"]}, {"role": "user", "content": spec["user"]}]
    mon = ContractMonitor(contract_for(kind)) if mode == "contract" else None

    calls = 0
    nudges = 0
    claimed_done = False
    blocked = False
    stopped_early = False
    t0 = time.time()

    def nudge(text: str) -> None:
        messages.append({"role": "assistant", "content": "DONE"})
        messages.append({"role": "user", "content": text})

    for step in range(max_steps):
        data = chat(base_url, model, messages, TOOLS, 0.7)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls_now, _ = parse_action(msg)

        if not calls_now:
            if mon is not None:
                violation = mon.before_stop(w, done=spec["done"](w))
                if violation and nudges < 3:
                    nudges += 1
                    nudge(f"Not done: progress={spec['progress'](w):.2f}. {mon.guidance(w)} Keep going.")
                    continue
                if violation:
                    blocked = True
                    break
            claimed_done = True
            break

        for call in calls_now[:2]:
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw = fn.get("arguments")
            args = raw if isinstance(raw, dict) else json.loads(raw or "{}")

            if mon is not None:
                churn = mon.before_call(name, args, w)
                if churn:
                    messages.append({"role": "assistant", "content": json.dumps({"name": name, "arguments": args})})
                    messages.append({"role": "user", "content": f"Refused: {churn.message}. Do something else."})
                    continue

            try:
                result = tools.call(name, args)
            except Exception as exc:  # noqa: BLE001
                result = f"error: {exc}"
            calls += 1
            messages.append({"role": "assistant", "content": json.dumps({"name": name, "arguments": args})})
            messages.append({"role": "user", "content": f"Result of {name}: {str(result)[:800]}"})

            if mon is not None:
                violation = mon.observe(w)
                if violation:
                    if nudges < 3:
                        nudges += 1
                        nudge(f"You are not advancing (progress={spec['progress'](w):.2f}). {mon.guidance(w)}")
                        mon.history.clear()
                    else:
                        stopped_early = True
                        break
        if stopped_early or blocked:
            break

    correct = bool(spec["done"](w))
    return {
        "kind": kind,
        "seed": seed,
        "mode": mode,
        "correct": correct,
        "false_done": claimed_done and not correct,
        "blocked": blocked,
        "calls": calls,
        "progress": round(spec["progress"](w), 3),
        "early_stop": stopped_early,
        "wall_s": round(time.time() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--per-task", type=int, default=5)
    ap.add_argument("--max-steps", type=int, default=14)
    ap.add_argument("--out", default=os.path.join(HERE, "ab_results.json"))
    args = ap.parse_args()

    rows = []
    t0 = time.time()
    for kind in TASKS:
        for mode in ("baseline", "contract"):
            for seed in range(args.per_task):
                row = run(kind, seed, mode, args.base_url, args.model, args.max_steps)
                rows.append(row)
                print(
                    f"{kind:8s} {mode:9s} seed={seed} correct={row['correct']!s:5s} false_done={row['false_done']!s:5s} "
                    f"blocked={row['blocked']!s:5s} calls={row['calls']:2d} progress={row['progress']:.2f}",
                    flush=True,
                )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)

    print(f"\n{len(rows)} runs in {time.time()-t0:.0f}s -> {args.out}")
    for kind in TASKS:
        for mode in ("baseline", "contract"):
            sub = [r for r in rows if r["kind"] == kind and r["mode"] == mode]
            ok = sum(r["correct"] for r in sub)
            fd = sum(r["false_done"] for r in sub)
            bl = sum(r["blocked"] for r in sub)
            calls = sum(r["calls"] for r in sub) / len(sub)
            print(f"  {kind:8s} {mode:9s} correct={ok}/{len(sub)}  false_done={fd}  blocked={bl}  calls/run={calls:.1f}")


if __name__ == "__main__":
    main()
