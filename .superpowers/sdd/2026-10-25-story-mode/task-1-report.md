# Task 1 Report

## Changes

- Added `story_schema.py` with bounded scene normalization and safe fallback scenes.
- Added `story_svg.py` with fixed SVG primitives and escaped labels.
- Added `story_store.py` with private story directories, atomic writes, manifest recovery, ordering, and traversal-safe IDs.
- Added `tests/story_smoke.py` covering normalization, fallback, SVG escaping, permissions, persistence recovery, and traversal rejection.

## Verification

- `.venv/bin/python tests/story_smoke.py` — passed (`All checks passed!`)
- `.venv/bin/ruff check --config apps/device-mac/pyproject.toml apps/device-mac/dongdong/story_schema.py apps/device-mac/dongdong/story_svg.py apps/device-mac/dongdong/story_store.py tests/story_smoke.py` — passed
- `.venv/bin/ruff format --check --config apps/device-mac/pyproject.toml ...` — passed after formatting
- `git diff --check` — passed

## Concerns

- Bundled FFmpeg path validation is owned by Task 3; this task does not add a placeholder binary or helper before that contract exists.

## Fix Report (2026-10-25)

- Hardened `scene_to_svg` with its own numeric geometry/timing coercion, fixed kind/color allowlists, bounded element count, and escaped labels so direct hostile dictionaries cannot inject SVG attributes or external URLs.
- Changed manifest and recovery scene ordering to numeric `scene-N` order, while retaining insertion order for other valid IDs; symlinked manifest and scene files are rejected or skipped.
- Expanded smoke coverage for direct renderer hostility, coordinate/color/timing limits, file permissions, manifest and scene symlink traversal, and `scene-2` versus `scene-10` ordering.
- Commit: `921012b0180c58bb76a11a94e7c0cafa00db6ec4` (`fix: harden story svg and persistence boundaries`).
