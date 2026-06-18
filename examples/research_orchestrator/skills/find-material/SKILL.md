---
name: find-material
description: Locate where the materials/data/code for a remembered experiment or topic live across the workspace ("which experiment was that, and where are its files?") via explore/search sub-agents; synthesize a located-evidence answer.
---

# find-material — "where is the stuff for experiment X?"

Use when the user half-remembers an experiment/result and needs to find the
related files (data, scripts, figures, notes, drafts) in the workspace.

## Procedure
1. PIN THE QUERY. Capture every clue the user gives — approximate date, method,
   dataset name, a figure they remember, a phrase from the writeup, a result
   value. Turn it into concrete search signals.
2. FAN OUT (delegate, read-only). Dispatch search/explore workers across the
   candidate locations:
   `sys_session_send(agent="claude_code"|"codex"|"gemini",
   title="find-<topic>", args={purpose:"search", input:"Find files related to
   <experiment/topic> using these clues: <clues>. Search <paths>. Return every
   hit as path + why it matches + a one-line summary. Read-only."})`. Use two
   different vendors / different subtrees in parallel for breadth.
3. DISAMBIGUATE. If several candidates match, dispatch an analyze worker to
   open the top hits and confirm which truly corresponds to the remembered
   experiment (matching the recalled number/figure/method), with evidence.
4. SYNTHESIZE. Return a ranked answer: the most likely location(s), the
   supporting evidence (path + matching detail), and any near-misses, so the
   user can confirm.

## Notes
- Prefer recall over precision first (cast wide), then narrow in step 3.
- If nothing matches, say so explicitly and report where you looked — do not
  guess a path.
