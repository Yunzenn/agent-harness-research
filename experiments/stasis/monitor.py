"""Detect stasis without a verifier.

Two candidate detectors, and why the second one is the right shape:

1. consecutive-identical: flag when K steps in a row look identical.
   Fails on the most common stall pattern — `read -> write -> read -> write` —
   because the signature alternates between two values forever.

2. novelty-window: flag when no *new* (world, result) signature has appeared for
   K steps. A loop that keeps cycling through the same two or three states is
   exactly what "not making progress" looks like from the outside.

No error signal, no judge, no verifier: only the world delta and the tool result.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .world import fingerprint


def _hash(value) -> str:
    return hashlib.sha1(repr(value).encode()).hexdigest()[:10]


@dataclass
class StasisMonitor:
    k: int = 4
    mode: str = "novelty"  # novelty | consecutive
    seen: set = field(default_factory=set)
    last_novel_step: int = -1
    detected_at: int | None = None
    reason: str = ""
    steps_without_novelty: int = 0

    def observe(self, step: int, world_state: dict, tool_result) -> bool:
        sig = (fingerprint(world_state), _hash(tool_result))
        novel = sig not in self.seen
        self.seen.add(sig)

        if novel:
            self.last_novel_step = step
            self.steps_without_novelty = 0
        else:
            self.steps_without_novelty += 1

        if self.detected_at is not None:
            return True
        if self.mode == "novelty" and step - self.last_novel_step >= self.k:
            self.detected_at = step
            self.reason = f"{self.k} steps without a new (world, result) signature"
            return True
        return False

    @property
    def stalled(self) -> bool:
        return self.detected_at is not None

    def summary(self) -> dict:
        return {
            "k": self.k,
            "mode": self.mode,
            "detected_at": self.detected_at,
            "reason": self.reason,
            "distinct_signatures": len(self.seen),
        }
