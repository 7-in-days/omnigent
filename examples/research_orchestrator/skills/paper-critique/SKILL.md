---
name: paper-critique
description: Get independent adversarial critiques of a research idea or draft from multiple different-vendor sub-agents (Claude / GPT / Gemini), then synthesize the strengths, weaknesses, risks, and concrete revision suggestions.
---

# paper-critique — independent adversarial review of ideas/drafts

Use to pressure-test an idea, hypothesis, framing, or paper draft before
committing to it — getting genuinely independent critiques rather than one
model's view.

## Procedure
1. SCOPE THE CRITIQUE. Identify the target (idea / abstract / section / full
   draft at <FILE PATH>) and the lens(es): novelty, soundness of method,
   statistics, threats to validity, related-work gaps, clarity, overclaiming.
2. FAN OUT TO DIFFERENT VENDORS (parallel). Dispatch the SAME draft/idea to
   TWO+ different-vendor critique workers:
   `sys_session_send(agent="claude_code"|"codex"|"gemini",
   title="critique-<target>", args={purpose:"critique", input:"Adversarially
   critique <target> at <FILE PATH> on <lenses>. Return STRENGTHS, WEAKNESSES,
   RISKS/THREATS-TO-VALIDITY, and CONCRETE revision suggestions, each with the
   specific passage/result it refers to. Be a skeptical reviewer, not a
   cheerleader."})`. Give each the identical path.
3. SYNTHESIZE (orchestrator). Merge the critiques:
   - Consensus weaknesses (flagged by multiple vendors) -> top-priority fixes.
   - Unique points -> list with which vendor raised them.
   - Contradictions between reviewers -> surface both; let the human judge.
4. ACTION LIST. Produce a prioritized, concrete revision checklist (what to
   change, where, why). If revisions are prose, you may draft them; if they
   need new analysis/experiments, dispatch analyze/implement workers.
5. REPORT. Deliver the synthesized critique + prioritized action list, with
   per-point provenance (which reviewer(s) raised it).

## Notes
- Independence matters: distinct vendors reduce single-model blind spots.
- Keep critiques tied to specific passages/results — reject vague feedback.
- Distinguish "this is wrong" (fix) from "this is unclear" (clarify) from
  "this is a risk" (caveat/limitation).
