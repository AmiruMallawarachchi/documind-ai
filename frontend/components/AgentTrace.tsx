"use client";

/**
 * components/AgentTrace.tsx — Collapsed by default: the steps that produced this answer.
 *
 * This is the debug view that makes a multi-agent answer inspectable — which passages were found, whether
 * the critic rejected a draft — and it is exactly what a reader wants when an answer looks evasive, and
 * noise when it does not. So it is one short line until asked.
 *
 * The labels come from `lib/trace`, which is where the fall-through for an unknown node name lives.
 */

import { useState } from "react";

import { FOCUS } from "@/components/ui/tokens";
import { traceLabel } from "@/lib/trace";

export default function AgentTrace({ steps }: { steps: string[] }) {
  const [show, setShow] = useState(false);

  return (
    <div className="text-[0.7rem] text-zinc-500">
      <button
        type="button"
        onClick={() => setShow((value) => !value)}
        className={`hover:text-zinc-300 ${FOCUS}`}
        aria-expanded={show}
      >
        {show ? "Hide" : "Show"} how this was answered
      </button>
      {show && (
        <ol className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1">
          {steps.map((step, position) => (
            <li
              key={`${step}-${position}`}
              className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono"
            >
              {traceLabel(step)}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
