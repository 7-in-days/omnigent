---
name: merge-materials
description: Consolidate scattered research materials (abstracts, results, experiment fragments, notes) into an organized folder structure or a single merged document, via explore + analyze sub-agents; the orchestrator synthesizes the final merge.
---

# merge-materials — consolidate scattered research

Use when the user has fragmented materials (drafts, abstracts, result tables,
experiment notes, figures spread across folders) and wants them merged into a
clean structure or one document.

## Procedure
1. SCOPE. Confirm the source location(s) and the target shape: an organized
   folder layout, a single merged Markdown/doc, or both. Ask only if the target
   is ambiguous.
2. INVENTORY (delegate, read-only). Dispatch an explore worker:
   `sys_session_send(agent="claude_code"|"codex"|"gemini",
   title="inventory-<topic>", args={purpose:"explore", input:"List every
   material file under <paths> relevant to <topic>; for each give path, type
   (abstract/result/figure/notes/data), a one-line summary, and date if
   knowable. Read-only."})`. For large/mixed trees, fan out two vendors over
   different subtrees and merge their inventories.
3. EXTRACT / NORMALIZE (delegate, analyze). For each cluster, dispatch an
   analyze worker to pull the salient content (key claims, numbers, figure/table
   captions, method notes) with citations (file path + page/section). Send
   figures/scans to the `gemini` worker (multimodal). Pass FILE PATHS, never
   attachments.
4. SYNTHESIZE (orchestrator, non-code authoring). YOU assemble the merged
   document or folder README from the workers' structured extracts — this is
   prose authoring you do directly. Preserve provenance: every merged item
   links back to its source path. Flag duplicates and conflicts for the human.
5. WRITE-OUT. If the deliverable is a reorganized folder or any file the
   workers must create/move, that's an implement task — dispatch a worker to
   make the changes in a worktree and open a PR; you do not move data files
   yourself.
6. REPORT. Give the human the merged artifact (or PR), a provenance map
   (merged item -> source), and a list of duplicates/conflicts to resolve.

## Notes
- Never silently drop a fragment — unresolved/duplicate items go in an
  "open items" section for the human.
- Keep raw sources intact; merge into new files rather than overwriting.
