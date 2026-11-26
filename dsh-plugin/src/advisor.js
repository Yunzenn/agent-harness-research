/**
 * loopkit's advisor, ported to plain JavaScript so a DSH plugin can call it
 * without a Python runtime. Same rules as loopkit/advisor.py — keep them in sync.
 *
 * The advisor is deliberately a rule engine, not a model: every decision must be
 * explainable and arguable in a design review.
 */

const HIGH_DENSITY = new Set(["code", "render", "schema"]);
const LOW_DENSITY = new Set(["rubric", "none", "human"]);

export const ORACLES = ["code", "render", "schema", "rubric", "human", "none"];
export const BLAST_RADII = ["read", "write", "irreversible"];
export const LIFETIMES = ["session", "hours", "days"];
export const HETEROGENEITIES = ["single", "few", "many"];

/** @typedef {Object} Brief
 * @property {string} [goal]
 * @property {string} [oracle]
 * @property {boolean} [reversible]
 * @property {string} [blastRadius]
 * @property {string} [lifetime]
 * @property {string} [heterogeneity]
 * @property {boolean} [subtasksKnown]
 * @property {number} [fanout]
 * @property {number} [latencyBudgetS]
 * @property {number} [costBudgetUsd]
 */

function normalized(brief) {
  const b = brief || {};
  const oracle = ORACLES.includes(b.oracle) ? b.oracle : "none";
  return {
    goal: b.goal ?? "",
    oracle,
    reversible: b.reversible ?? true,
    blastRadius: BLAST_RADII.includes(b.blastRadius) ? b.blastRadius : "read",
    lifetime: LIFETIMES.includes(b.lifetime) ? b.lifetime : "session",
    heterogeneity: HETEROGENEITIES.includes(b.heterogeneity) ? b.heterogeneity : "single",
    subtasksKnown: b.subtasksKnown ?? true,
    fanout: Number.isFinite(b.fanout) ? Math.max(1, Math.trunc(b.fanout)) : 1,
    latencyBudgetS: Number.isFinite(b.latencyBudgetS) ? b.latencyBudgetS : 60,
    costBudgetUsd: Number.isFinite(b.costBudgetUsd) ? b.costBudgetUsd : 1,
  };
}

/**
 * @param {Brief} brief
 * @returns {{topology:string, pattern:string, concurrency:number, toolPolicy:string,
 *            gates:string[], budgets:Object, tiers:Object, reasons:string[], warnings:string[]}}
 */
export function advise(brief) {
  const b = normalized(brief);
  const reasons = [];
  const warnings = [];

  const durable = b.lifetime === "hours" || b.lifetime === "days";
  const high = HIGH_DENSITY.has(b.oracle);
  const noOracle = LOW_DENSITY.has(b.oracle);

  // 1. control flow
  let pattern;
  if (!b.subtasksKnown && b.fanout >= 3) {
    pattern = "fanout";
    reasons.push(`子任务数事先不可知且预计 ≥3（${b.fanout}），用编排者-工人扇出而不是写死流程`);
  } else if (b.heterogeneity === "many") {
    pattern = "route";
    reasons.push("需求分成多类且每类工具不同：先分类再进窄车道，避免一套工具打天下");
  } else if (noOracle) {
    pattern = "chain";
    reasons.push(`判据在人的一侧（oracle=${b.oracle}）：用带闸门的固定链，不让模型自动收敛`);
  } else if (high && b.fanout >= 3) {
    pattern = "fanout";
    reasons.push("判据可自动验证，且子任务彼此独立：分节并行，结果用同一契约汇总");
  } else if (high) {
    pattern = "plan_verify";
    reasons.push("判据可自动验证且任务跨多步：先出计划工件，再做独立校验");
  } else {
    pattern = "react";
    reasons.push("有可用判据、副作用可控：有界 ReAct 足够，最便宜");
  }

  const TOPOLOGY = { chain: "L1", route: "L2", react: "L3", plan_verify: "L4", fanout: "L5" };
  let topology = TOPOLOGY[pattern];
  if (durable) {
    topology = "L7";
    reasons.push(`寿命=${b.lifetime}：拓扑之上必须叠持久化（检查点/幂等/唤醒）`);
  }

  // 2. concurrency
  let concurrency;
  if (pattern === "fanout") {
    concurrency = Math.max(1, Math.min(b.fanout, 8));
    if (b.fanout > 8) {
      warnings.push(`扇出 ${b.fanout} > 8：建议升级到 L6（脚本编排）并加分批，否则并发上限会变成瓶颈`);
    }
  } else if (pattern === "route") {
    concurrency = Math.max(1, Math.min(b.fanout, 4));
    reasons.push(`车道内并发 ${concurrency}；每条车道有独立步数上限，防止单条失控`);
  } else {
    concurrency = 1;
  }

  // 3. tool policy
  let toolPolicy;
  if (b.blastRadius === "read") {
    toolPolicy = "read-only";
    reasons.push("副作用面只有读：沙箱可以放到最宽，审批可以全关");
  } else if (b.heterogeneity === "many") {
    toolPolicy = "narrow-per-lane";
    reasons.push("每类需求只暴露它需要的 3–6 个工具：省 token，也减少选错");
  } else {
    toolPolicy = "narrow";
    reasons.push("写操作单独授权，读工具与写工具在 schema 层就分开");
  }

  // 4. gates
  const gates = [];
  if (b.blastRadius === "irreversible") {
    gates.push("approval: 每个不可逆动作（代码路径，非提示）");
    reasons.push("存在不可逆副作用：闸门必须由控制层执行，且拒绝不可被提示词推翻");
  } else if (b.blastRadius === "write") {
    gates.push("approval: 不可逆子集 + dry-run");
  }
  if (noOracle) gates.push("human: 产出必须经人确认才算完成");
  if (durable) gates.push("checkpoint: 每步落盘", "idempotency: 同一事件只处理一次");
  if (!b.reversible && b.blastRadius !== "irreversible") {
    warnings.push("标记为不可逆但 blast_radius 不是 irreversible：两者矛盾，请确认");
  }

  // 5. budgets
  const STEPS = { react: 8, plan_verify: 14, chain: 12, route: 6, fanout: 3 };
  const CALLS = { react: 24, plan_verify: 40, chain: 30, route: 16, fanout: 8 };
  const maxSteps = STEPS[pattern];
  let maxToolCalls = CALLS[pattern];
  if (pattern === "fanout") maxToolCalls *= concurrency;
  const maxTokensIn = Math.trunc(Math.max(20000, Math.min(2000000, b.costBudgetUsd * 200000)));
  const budgets = {
    max_steps: maxSteps,
    max_tool_calls: maxToolCalls,
    max_tokens_in: maxTokensIn,
    max_seconds: Math.round(b.latencyBudgetS * 10) / 10,
  };
  if (b.costBudgetUsd < 0.05) {
    warnings.push("成本预算 < $0.05：几乎只够单发调用，任何循环都会超支");
  }

  // 6. model tiers
  const tiers = {};
  if (b.heterogeneity === "many") {
    tiers.router = "small";
    tiers.exec = "large";
    reasons.push("分类用便宜模型（20–50ms 级），执行用强模型：路由不该按强模型计费");
  } else if (pattern === "fanout") {
    tiers.worker = "small";
    tiers.merge = "large";
    reasons.push("工人只做抽取/摘要，合并再上强模型");
  } else {
    tiers.exec = "large";
  }

  // 7. sanity warnings
  if (b.oracle === "none") {
    warnings.push("没有判据：不要指望模型自评收敛，每次运行都必须以人的确认结束");
  }
  if (noOracle && b.fanout > 4) {
    warnings.push("低判据密度 + 大扇出：错误会被并行放大，建议先小规模验证判据");
  }
  if (b.latencyBudgetS < 10 && (pattern === "plan_verify" || pattern === "fanout")) {
    warnings.push("延迟预算 < 10s 与所选拓扑冲突：多阶段必然超时");
  }

  return { topology, pattern, concurrency, toolPolicy, gates, budgets, tiers, reasons, warnings };
}

/** Render a plan as Markdown for a chat surface. */
export function formatPlan(plan) {
  const lines = [];
  lines.push(`### ${plan.topology} / ${plan.pattern}`);
  lines.push("");
  lines.push("| 项 | 值 |");
  lines.push("|---|---|");
  lines.push(`| 拓扑 | ${plan.topology} / ${plan.pattern} |`);
  lines.push(`| 并发 | ${plan.concurrency} |`);
  lines.push(`| 工具面 | ${plan.toolPolicy} |`);
  lines.push(`| 闸门 | ${plan.gates.length ? plan.gates.join("; ") : "—"} |`);
  lines.push(`| 预算 | ${Object.entries(plan.budgets).map(([k, v]) => `${k}=${v}`).join(", ")} |`);
  lines.push(`| 模型分层 | ${Object.entries(plan.tiers).map(([k, v]) => `${k}→${v}`).join(", ")} |`);
  lines.push("");
  lines.push("**理由**");
  for (const r of plan.reasons) lines.push(`- ${r}`);
  if (plan.warnings.length) {
    lines.push("");
    lines.push("**注意**");
    for (const w of plan.warnings) lines.push(`- ⚠️ ${w}`);
  }
  return lines.join("\n");
}
