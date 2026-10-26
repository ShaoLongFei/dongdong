# Story Mode Implementation Plan

> 历史计划，归档于 2026-10-26：故事功能已实现，后续质量修复见 Git 提交记录和 `docs/decisions.md`。以下保留原始设计与复选框，不作为当前未完成事项清单；实际行为以代码及 `docs/README.md` 为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a low-latency story mode that turns voice utterances into validated hand-drawn SVG scenes, persists them, and exports completed stories as PNG, PDF, or MP4.

**Architecture:** Keep story processing beside `WebState`, with a focused `StorySession` responsible for buffering, scene generation, persistence, and event delivery. Extend the existing FastAPI app with story APIs and an SSE stream; the React console renders a FIFO SVG scene queue. Export work runs in a background thread and uses project-local conversion tools, including a checked-in arm64 FFmpeg binary.

**Tech Stack:** Python 3.11+, FastAPI, asyncio, OpenAI-compatible `httpx` client, stdlib JSON/path/threading, React/Vite, SVG/CSS animations, project-local FFmpeg.

**Spec:** `docs/superpowers/specs/2026-10-25-story-mode-design.md`

## Global Constraints

- Keep Python files under 300 lines; split story schema, rendering, session, and export responsibilities.
- Use standard library first; do not add image/video dependencies for the real-time path.
- Store stories under `~/.dongdong/stories/<story_id>/` with mode-appropriate permissions.
- Story text must never enter the normal programming agent while story mode is active.
- SVG is generated only from fixed components; escape all user/model text and reject external resources/scripts.
- Story scenes use 1280×720, `#F5EBD7`, 3–8 elements, constrained colors and serialized reveal timing.
- MP4 must resolve an executable under `tools/ffmpeg/`, never the system `PATH`.
- Do not overwrite unrelated uncommitted work in the current checkout.
- Add only meaningful protocol/persistence/security tests in `tests/story_smoke.py`; no UI snapshot suite.

## Review Focus

- A short STT fragment must not wait forever: the buffer flushes on event words, pause, 2-second timeout, or 40-character cap. Test this in `tests/story_smoke.py`.
- Malformed or malicious scene JSON must degrade to a safe default scene and never inject SVG markup. Test schema normalization and SVG escaping.
- Story IDs and export paths must not escape the private story root, including symlink/`..` attempts. Test path resolution.
- Restart/reload must recover a persisted manifest and scenes without trusting arbitrary filenames. Test manifest loading.
- MP4 export must fail clearly when the bundled binary is missing or mismatched, without falling back to system FFmpeg. Test executable resolution.

### Task 1: Scene protocol, safe SVG components, and story persistence

**Files:**
- Create: `apps/device-mac/dongdong/story_schema.py`
- Create: `apps/device-mac/dongdong/story_svg.py`
- Create: `apps/device-mac/dongdong/story_store.py`
- Create: `tests/story_smoke.py`
- Modify: `apps/device-mac/dongdong/config.py` only if a stories-root helper is needed

**Interfaces:**
- `story_schema.normalize_scene(raw: dict | str, story_text: str, scene_id: str) -> dict`
- `story_schema.default_scene(story_text: str, scene_id: str) -> dict`
- `story_svg.scene_to_svg(scene: dict) -> str`
- `story_store.StoryStore(root: Path)` with `create()`, `save_scene()`, `load()`, `list_stories()`, and `save_manifest()` methods

- [ ] Write protocol tests for valid normalization, invalid JSON fallback, coordinate/color/element limits, SVG escaping, private directory permissions, path traversal rejection, manifest recovery, and bundled FFmpeg path validation helper.
- [ ] Run `.venv/bin/python tests/story_smoke.py` and confirm the new tests fail because the modules do not exist.
- [ ] Implement normalization with fixed allowed kinds/colors, integer in-bounds rectangles, serial reveal timings, 3–8 element cap, and a default scene for all invalid model output.
- [ ] Implement fixed SVG primitives for `tree`, `rabbit`, `person`, `house`, `sun`, `object`, `action_lines`, and `simple_shape`; escape labels/text and emit no external URLs, scripts, or event handlers.
- [ ] Implement `StoryStore` with `0700` story directories, atomic JSON/SVG writes, safe story ID validation, manifest metadata, and scene ordering.
- [ ] Run the story smoke file and the existing `scripts/check.sh` checks that do not require a frontend build.
- [ ] Commit as `feat: add story scene protocol and persistence`.

### Task 2: Real-time story session and model scene generation

**Files:**
- Create: `apps/device-mac/dongdong/story.py`
- Modify: `apps/device-mac/dongdong/web_runtime.py`
- Modify: `apps/device-mac/dongdong/linx.py`
- Modify: `apps/device-mac/dongdong/web_server.py`
- Modify: `tests/story_smoke.py`

**Interfaces:**
- `StorySession.start() -> str`, `stop() -> Awaitable[dict]`, `accept_text(text: str) -> None`, `state() -> dict`, `events(since: int) -> list[dict]`, `close() -> Awaitable[None]`
- `WebState.story_text(connection: str, role: str, text: str) -> None`
- `WebState.story_mode(enabled: bool) -> Awaitable[dict]`

- [ ] Add tests for adaptive buffering, explicit start/stop phrases, fallback scene generation when the model fails, and event cursor behavior.
- [ ] Run the focused story tests and confirm the new session APIs fail.
- [ ] Implement `StorySession` with a 2-second flush timer, event-word and pause handling, 40-character cap, concurrent bounded scene tasks, role/character continuity state, and finite stop draining.
- [ ] Build the scene prompt from story text and call the existing OpenAI-compatible model client through a small JSON-only request helper; parse and normalize with Task 1 modules.
- [ ] Persist each scene JSON/SVG before publishing a scene event; publish state/error/export events through an in-memory cursor list that SSE can poll.
- [ ] Wire `WebState` to own one story session, prevent regular agent starts while recording, route voice STT to story mode, and recognize Chinese start/stop phrases without creating ordinary chat messages.
- [ ] Add FastAPI routes for mode/state/events, story listing/details, and replay; ensure all routes stay same-origin and use safe IDs.
- [ ] Run story smoke plus the existing Python checks.
- [ ] Commit as `feat: add realtime story session APIs`.

### Task 3: Export jobs and bundled FFmpeg contract

**Files:**
- Create: `apps/device-mac/dongdong/story_export.py`
- Create: `tools/ffmpeg/darwin-arm64/VERSION`
- Create: `tools/ffmpeg/LICENSE`
- Create: `tools/ffmpeg/darwin-arm64/ffmpeg` (checked-in executable supplied after license/source verification)
- Modify: `apps/device-mac/dongdong/web_server.py`
- Modify: `tests/story_smoke.py`
- Modify: `docs/decisions.md`

**Interfaces:**
- `story_export.bundled_ffmpeg() -> Path`
- `story_export.export_story(story_dir: Path, fmt: str) -> Path`
- `story_export.ExportManager.submit(story_id: str, fmt: str) -> str`
- `story_export.ExportManager.status(job_id: str) -> dict`

- [ ] Add tests for format validation, project-local FFmpeg resolution, missing/non-executable binary errors, and export job terminal states.
- [ ] Run the focused tests and confirm export APIs fail.
- [ ] Implement SVG-to-PNG/PDF conversion using already available local capabilities or a clearly reported unsupported error; never affect source scenes.
- [ ] Implement MP4 frame generation/replay and invoke only the checked-in FFmpeg path with fixed 1280×720/30fps H.264/AAC arguments.
- [ ] Add an export manager using a bounded background executor and status events; preserve source files on every failure.
- [ ] Add story export routes and same-origin download responses.
- [ ] Verify the selected FFmpeg binary’s license and checksum, record version/source/SHA-256 in `docs/decisions.md`, and make the executable bit explicit.
- [ ] Run story smoke and Python checks. If the binary cannot be legally or practically checked in, stop and report the exact blocker before adding a placeholder binary.
- [ ] Commit as `feat: add story exports with bundled ffmpeg`.

### Task 4: Story canvas and mode controls in the Web console

**Files:**
- Create: `apps/web/src/StoryCanvas.jsx`
- Modify: `apps/web/src/main.jsx`
- Modify: `apps/web/src/api.js`
- Modify: `apps/web/src/style.css`

**Interfaces:**
- `StoryCanvas({ state, onModeChange, onExport, onReplay })` renders the current story state and consumes `/api/story/events`.

- [ ] Add the story tab/control surface with `开始讲故事` and `结束讲故事`, current mode/status, subtitle, scene count, replay, and export buttons.
- [ ] Implement an EventSource consumer with cursor/reconnect behavior and a FIFO queue of scenes.
- [ ] Render the server-provided SVG inside a fixed 16:9 paper canvas; animate each element using its reveal timing and retain completed strokes.
- [ ] Show model/loading/fallback/error states and disable export until the story is `ready`.
- [ ] Add responsive styles consistent with the existing dark console and warm paper canvas; honor reduced motion.
- [ ] Run `npm ci && npm run build` in `apps/web` and inspect the built app for console/build errors.
- [ ] Commit as `feat: add story mode web canvas`.

### Task 5: Voice commands, recovery, documentation, and integration verification

**Files:**
- Modify: `apps/device-mac/dongdong/voice.py` only if lifecycle cleanup requires it
- Modify: `apps/device-mac/dongdong/web_runtime.py` and `apps/device-mac/dongdong/web_server.py` for restart cleanup
- Modify: `docs/architecture.md`
- Modify: `docs/decisions.md`
- Modify: `docs/README.md`
- Modify: `scripts/check.sh` if the smoke command needs registration
- Modify: `tests/story_smoke.py`

- [ ] Add an integration test for mode transitions: button/API start, voice start/stop phrase routing, story completion, and ordinary chat remaining available afterward.
- [ ] Load the latest manifest after WebState initialization and expose a recoverable story list without automatically resuming microphone capture.
- [ ] Ensure closing the app cancels timers/tasks and leaves persisted scenes intact.
- [ ] Document the story APIs, story directory, export requirements, FFmpeg license/source, and manual verification flow.
- [ ] Run `./scripts/check.sh`, `npm run build`, and a local FastAPI smoke request sequence for story mode and export status.
- [ ] Review `git diff --check`, `git status`, and all changed files for the 300-line rule and unrelated edits.
- [ ] Commit as `docs: document story mode integration`.

## Self-Review

- Spec coverage: scene protocol/persistence (Task 1), adaptive real-time session and voice/API boundary (Task 2), all three exports and bundled FFmpeg (Task 3), canvas playback and controls (Task 4), restart/lifecycle/docs/integration (Task 5).
- No placeholders remain; the only conditional step is the explicit license/checksum gate for the requested binary, which must report a blocker rather than silently add an unusable file.
- Interfaces are consistent: Task 2 publishes scenes from Task 1; Task 3 consumes Task 1 storage and is called by Task 2 routes; Task 4 consumes Task 2 SSE/API contracts; Task 5 verifies the combined flow.
- Review focus inputs are pinned to Task 1, Task 2, and Task 3 tests; UI behavior is manually verified as required by the project guidelines.
