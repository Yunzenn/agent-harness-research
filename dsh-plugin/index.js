/**
 * DSH plugin: expose loopkit's advisor as a tool inside a dsh session.
 *
 *   user: "这个退款工单该怎么编排？" -> model calls advise_workflow -> gets a plan
 *
 * The tool is pure and cheap: no model call inside, no network, no file access.
 * It exists so the *harness* can reason about topology without a Python runtime.
 */

import { defineTool } from "@deepseek-ai/dsh-tools";
import { advise, formatPlan, ORACLES, BLAST_RADII, LIFETIMES, HETEROGENEITIES } from "./src/advisor.js";

export const name = "dsh-loopkit";
export const inject = ["tools"];

const list = (values) => values.join(" | ");

const DESCRIPTION = [
  "Recommend an agent workflow topology (and its budgets, tool surface, gates, model tiers) from a business brief.",
  "Use it when you must decide HOW to orchestrate a task: single ReAct loop, fixed chain, router, fan-out, or plan-execute-verify.",
  "It is a deterministic rule engine, not an LLM: same brief, same plan, with reasons you can argue with.",
  "Rules: verification density (oracle) decides whether the loop may close on the model; blast radius decides approval gates; lifetime decides persistence.",
].join(" ");

export function apply(ctx) {
  ctx.tools.register(
    defineTool({
      name: "advise_workflow",
      description: DESCRIPTION,
      parameters: {
        goal: {
          type: "string",
          description: "One-line business goal. Echoed back; does not change the recommendation.",
        },
        oracle: {
          type: "string",
          description: `How correctness is judged: ${list(ORACLES)}. code/render/schema = high density; rubric/human/none = low.`,
        },
        blast_radius: {
          type: "string",
          description: `Worst side effect of one action: ${list(BLAST_RADII)}.`,
        },
        lifetime: {
          type: "string",
          description: `How long a run lives: ${list(LIFETIMES)}. hours/days force persistence (L7).`,
        },
        heterogeneity: {
          type: "string",
          description: `Request mix: ${list(HETEROGENEITIES)}. many = route first, then narrow tools per lane.`,
        },
        subtasks_known: {
          type: "boolean",
          description: "Whether the number of subtasks is known up front. false + fanout>=3 implies orchestrator-workers.",
        },
        fanout: {
          type: "integer",
          description: "How many independent items you expect to process (default 1).",
        },
        latency_budget_s: {
          type: "number",
          description: "Wall-clock budget in seconds (default 60).",
        },
        cost_budget_usd: {
          type: "number",
          description: "Token budget expressed in USD (default 1.0); converted to max input tokens.",
        },
      },
      output: {
        schema: { type: "string" },
        render: (_args, value) => [{ type: "text", text: value }],
      },
      async execute(args) {
        const notes = [];
        const pick = (value, allowed, field, fallback) => {
          if (value === undefined || value === null || value === "") return fallback;
          if (!allowed.includes(value)) {
            notes.push(`${field}='${value}' 不在 ${allowed.join("/")} 中，已按 '${fallback}' 处理`);
            return fallback;
          }
          return value;
        };

        const plan = advise({
          goal: args.goal ?? "",
          oracle: pick(args.oracle, ORACLES, "oracle", "none"),
          blastRadius: pick(args.blast_radius, BLAST_RADII, "blast_radius", "read"),
          lifetime: pick(args.lifetime, LIFETIMES, "lifetime", "session"),
          heterogeneity: pick(args.heterogeneity, HETEROGENEITIES, "heterogeneity", "single"),
          subtasksKnown: args.subtasks_known ?? true,
          fanout: args.fanout ?? 1,
          latencyBudgetS: args.latency_budget_s ?? 60,
          costBudgetUsd: args.cost_budget_usd ?? 1,
        });

        const head = notes.length ? notes.map((n) => `> ⚠️ ${n}`).join("\n") + "\n\n" : "";
        return head + formatPlan(plan);
      },
    }),
  );
}
