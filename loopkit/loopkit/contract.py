"""Progress contracts: make "done" and "advancing" explicit, then check them.

The experiment in `experiments/stasis/` showed the important part is not a smarter
stall detector — it is writing down what counts as progress:

    contract = Contract(
        name="resolve tickets",
        done_when=lambda w: all(v == "resolved" for v in w.tickets.values()),
        advances=lambda w: sum(v == "resolved" for v in w.tickets.values()) / len(w.tickets),
        never=lambda name, args, w: "already resolved" if w.tickets.get(args.get("tid")) == "resolved" else None,
    )

Three checks, each catching one real failure mode:

    premature_stop : the loop wants to end while done_when() is false
    drift          : advances() has not increased for `stall_steps` observations
    churn          : a call the contract forbids (e.g. rewriting the same artifact)

Nothing here calls a model. It is deterministic, cheap, and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Violation:
    kind: str  # premature_stop | drift | churn
    message: str
    at_step: int = 0
    suggestion: str = ""

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message}"


@dataclass
class Contract:
    name: str
    done_when: Callable[[Any], bool]
    advances: Callable[[Any], float]
    never: Optional[Callable[[str, dict, Any], Optional[str]]] = None
    stall_steps: int = 4


class ContractMonitor:
    """Observes a run and reports the first violation. Never raises."""

    def __init__(self, contract: Optional[Contract] = None, stall_steps: int = 4) -> None:
        self.contract = contract
        self.stall_steps = contract.stall_steps if contract else stall_steps
        self.history: list[float] = []
        self.violations: list[Violation] = []
        self.steps = 0

    # -- observations ------------------------------------------------------
    def observe(self, world: Any = None, *, value: Optional[float] = None) -> Optional[Violation]:
        """Record one step. Reads advances(world), or takes an explicit value."""
        if value is None:
            if self.contract is None:
                raise ValueError("observe() needs a contract or an explicit value=")
            value = float(self.contract.advances(world))
        self.steps += 1
        self.history.append(value)

        if len(self.history) >= self.stall_steps:
            window = self.history[-self.stall_steps :]
            if max(window) <= min(window) + 1e-9:
                return self._flag(
                    Violation(
                        "drift",
                        f"progress stayed at {value:.2f} for {self.stall_steps} steps",
                        self.steps,
                        "stop and re-plan, or change strategy; repeating the same action will not help",
                    )
                )
        return None

    def before_stop(self, world: Any = None, *, done: Optional[bool] = None) -> Optional[Violation]:
        """Call when the loop wants to finish."""
        if done is None:
            if self.contract is None:
                raise ValueError("before_stop() needs a contract or an explicit done=")
            done = bool(self.contract.done_when(world))
        if not done:
            return self._flag(
                Violation(
                    "premature_stop",
                    "the loop is ending but done_when() is still false",
                    self.steps,
                    "do not accept the model's 'done'; continue, or escalate to a human",
                )
            )
        return None

    def before_call(self, name: str, args: dict, world: Any = None) -> Optional[Violation]:
        if self.contract is None or self.contract.never is None:
            return None
        reason = self.contract.never(name, args, world)
        if reason:
            return self._flag(
                Violation("churn", f"{name}({args}) is forbidden: {reason}", self.steps, "skip the call and explain why")
            )
        return None

    # -- results -----------------------------------------------------------
    def _flag(self, violation: Violation) -> Violation:
        if not any(v.kind == violation.kind and v.message == violation.message for v in self.violations):
            self.violations.append(violation)
        return violation

    @property
    def progress(self) -> float:
        return self.history[-1] if self.history else 0.0

    def summary(self) -> dict:
        return {
            "contract": self.contract.name if self.contract else None,
            "steps": self.steps,
            "progress": self.progress,
            "violations": [v.kind for v in self.violations],
        }
