import test from "node:test";
import assert from "node:assert/strict";
import { advise, formatPlan } from "../src/advisor.js";

test("human oracle: fixed chain, human sign-off, L1", () => {
  const p = advise({ goal: "grade essays", oracle: "human", blastRadius: "read" });
  assert.equal(p.pattern, "chain");
  assert.equal(p.topology, "L1");
  assert.ok(p.gates.some((g) => g.startsWith("human")));
});

test("unknown subtasks and many items: fan-out capped at 8, L6 warning", () => {
  const p = advise({ oracle: "code", subtasksKnown: false, fanout: 12 });
  assert.equal(p.pattern, "fanout");
  assert.equal(p.concurrency, 8);
  assert.ok(p.warnings.some((w) => w.includes("L6")));
});

test("irreversible blast radius requires an approval gate (narrow tools)", () => {
  const p = advise({ oracle: "rubric", blastRadius: "irreversible" });
  assert.ok(p.gates.some((g) => g.startsWith("approval")));
  assert.equal(p.toolPolicy, "narrow");
});

test("days: L7 with checkpoint and idempotency", () => {
  const p = advise({ oracle: "code", lifetime: "days" });
  assert.equal(p.topology, "L7");
  assert.ok(p.gates.some((g) => g.startsWith("checkpoint")));
  assert.ok(p.gates.some((g) => g.startsWith("idempotency")));
});

test("no oracle: warns that the model must not self-judge", () => {
  const p = advise({ oracle: "none" });
  assert.ok(p.warnings.some((w) => w.includes("没有判据")));
});

test("heterogeneous demand routes with a cheap router tier", () => {
  const p = advise({ oracle: "rubric", heterogeneity: "many", fanout: 4 });
  assert.equal(p.pattern, "route");
  assert.equal(p.tiers.router, "small");
});

test("invalid enum falls back instead of throwing", () => {
  const p = advise({ oracle: "nonsense", blastRadius: "???" });
  assert.equal(typeof p.pattern, "string");
  assert.ok(p.reasons.length > 0);
});

test("formatPlan renders a table, reasons and warnings", () => {
  const out = formatPlan(advise({ oracle: "code", blastRadius: "write" }));
  assert.match(out, /^\| 项 \| 值 \|/m);
  assert.match(out, /理由/);
  assert.ok(out.includes("plan_verify"));
});
