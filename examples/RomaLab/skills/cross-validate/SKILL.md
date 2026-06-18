---
name: cross-validate
description: Verify a figure, table, OCR extraction, number, or claim by fanning the SAME input out to two or more different-vendor sub-agents (Claude / GPT / Gemini) and reconciling their independent outputs; agreement corroborates, disagreement is flagged for resolution.
---

# cross-validate — independent multi-vendor verification

Use for any result that matters: a figure read, a table extraction, an OCR
transcription, a computed number, or a factual/claim check. The whole point is
INDEPENDENT agreement across different vendors.

## Procedure
1. DEFINE THE CHECK. State the exact artifact and the precise question (e.g.
   "extract the values + error bars from Figure 3 in <path>", "what is the n in
   Table 2 of <path>", "recompute the p-value from <data path> using <method>",
   "does claim X in <draft> match the result in <results path>?"). Pin the
   acceptance: what counts as a match.
2. FAN OUT TO DIFFERENT VENDORS (parallel, same input). Dispatch the SAME task
   to TWO+ different-vendor workers in one turn:
   `sys_session_send(agent="gemini", title="crossval-<artifact>",
   args={purpose:"analyze", input:"<the check> from <FILE PATH>"})` and the same
   to `claude_code` and/or `codex`. Give every worker the IDENTICAL file path.
   Prefer `gemini` for figures/tables/OCR (multimodal); always include at least
   one other vendor.
3. RECONCILE (orchestrator). Compare the independent outputs:
   - All agree -> report the corroborated value with the vendors that agreed.
   - They disagree -> do NOT average. Flag the discrepancy, show each vendor's
     answer + its cited evidence, and either dispatch a third vendor as a
     tiebreaker or escalate to the human with the specifics.
4. FOR CODE/RESULTS. If verification requires running analysis, the analyze
   worker does it in its worktree and returns numbers + method; never trust a
   single run for a decision-grade number — re-run on a second vendor.
5. REPORT. Give the verdict (corroborated / disputed), the per-vendor evidence,
   and a confidence note.

## Notes
- Independence is mandatory: never let one worker "review" its own extraction —
  the second opinion must be a DIFFERENT vendor.
- Record low-confidence reads (especially OCR/figures) as disputed, not
  silently resolved.
