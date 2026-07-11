# AGENTS.md

## Collaboration Rules

- Use Chinese when replying to the user unless they explicitly request another language.
- Read the existing code and follow local patterns before changing implementation.
- Keep changes scoped to the user's request. Do not perform unrelated refactors.
- Never revert user changes or untracked local files unless the user explicitly asks.
- Do not commit local secrets, `.DS_Store`, machine-specific files, or private run scripts.

## Superpowers And Verification

- When using Superpowers or splitting complex work into subtasks, do not run full test, lint, type-check, or browser verification after every subtask by default.
- Use risk-based verification instead:
  - For docs, rules, comments, plans, and other non-executable text changes, a targeted file-read or formatting sanity check is enough.
  - For isolated code changes, run the smallest relevant test or syntax check that proves that change.
  - For shared contracts, API behavior, auth, data access, build configuration, or UI flows, run targeted regression checks for the touched surface.
  - Run broader test suites only at integration checkpoints, before commit/push, or when the change has cross-module risk.
- If a relevant verification is intentionally skipped, state why and what risk remains.
- Prefer one final consolidated verification pass over repeated full-suite runs in every subtask.
