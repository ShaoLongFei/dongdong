# SDD ledger — plan: docs/superpowers/plans/2026-10-25-task-scheduler.md

Pre-flight: Task 1 outputs Store task APIs consumed by Tasks 2-4; Task 2 consumes AttemptResult and task projection; Task 3 consumes TaskManager submit/status/stop/resume; Task 4 extends Store and TaskManager states; Task 5 consumes API projection. Interfaces will be kept backward-compatible with existing Store runs/events.
Ruling: use the current codex/web-console checkout — it is a dedicated feature branch and the user explicitly authorized direct implementation; cost if wrong: changes would need relocation to a worktree.
