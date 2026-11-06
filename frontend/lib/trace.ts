/**
 * lib/trace.ts — The agent trace's node names, translated once.
 *
 * The backend streams the graph's own node names (`retrieval`, `critique`, …) and some of them carry a
 * detail after a colon — `retrieval:6` is six passages, `critique:reject` is a rejected draft. The names
 * are a wire format the graph can rename on a whim, so an unknown one falls through verbatim instead of
 * disappearing: a step the reader cannot map is still a step that happened.
 */

const STAGE_LABELS: Record<string, string> = {
  router: "routed",
  retrieval: "searched",
  synthesis: "drafted",
  critique: "verified",
  finalise: "answered",
  direct: "replied",
  guard: "checked corpus",
};

export function traceLabel(step: string): string {
  const [agent, detail] = step.split(":");
  const friendly = STAGE_LABELS[agent] ?? agent;
  return detail ? `${friendly} ${detail}` : friendly;
}
