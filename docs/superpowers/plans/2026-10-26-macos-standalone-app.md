# macOS Standalone App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task.

**Goal:** Build an Apple Silicon macOS `.app` that embeds the Python runtime and current Web UI, starts the local FastAPI service itself, and opens it in a native WebKit window.

**Architecture:** A small Swift AppKit/WKWebView launcher owns one bundled Python child process. The app bundle contains a relocatable CPython runtime, the `apps/device-mac` package, built static assets, FFmpeg and PortAudio resources. Runtime state remains in `~/.dongdong`; the launcher chooses an available loopback port and passes it to `dongdong web`.

**Tech Stack:** Swift 5/AppKit/WebKit, CPython 3.12 arm64, FastAPI/Uvicorn, React/Vite static output, shell build script.

**Spec:** User request to wrap the existing web app as an independent macOS app, targeting Apple Silicon arm64.

## Global Constraints

- Keep the existing Web UI and FastAPI API unchanged except for relocatable data paths.
- Do not require Node.js or system Python at runtime.
- Keep user data outside the read-only `.app` bundle.
- Keep new source files under 300 lines and use Chinese comments where the reason is non-obvious.
- Run `./scripts/check.sh` before claiming completion.

## Review Focus

- Existing service already listening on port 8766: launcher must select a different port and load its own process.
- Missing or broken bundled Python: launcher must show a readable startup error and never load a stale page.
- App termination while Python is running: child process must be terminated and waited for.
- Read-only bundle paths: memory and generated data must resolve to `~/.dongdong`.
- Fresh build without Node installed: build script must fail early with an actionable message, while the finished app needs no Node.

### Task 1: Relocatable data paths

**Files:**
- Modify: `apps/device-mac/dongdong/memory.py`

- [ ] Add an optional `DONGDONG_MEMORY_DIR` override while preserving the current repository default for development and tests.
- [ ] Run the existing smoke and memory checks.

### Task 2: Native launcher

**Files:**
- Create: `apps/device-mac/macos/main.swift`
- Create: `apps/device-mac/macos/Info.plist`

- [ ] Implement AppKit window, WebKit view, dynamic loopback port selection, bundled Python process launch, readiness polling, error alert, and child cleanup.
- [ ] Keep the launcher under 300 lines and use bundle-relative paths only.

### Task 3: Standalone bundle builder

**Files:**
- Create: `scripts/build-macos-app.sh`
- Modify: `README.md`
- Modify: `docs/runbook.md`

- [ ] Build the React static assets, fetch/extract the arm64 CPython runtime, copy package/site-packages/assets into `Contents/Resources`, and compile/sign the `.app`.
- [ ] Add a `--skip-frontend` option for environments where static assets are already built.
- [ ] Add documented build/run commands and the external prerequisites that remain intentional.

### Task 4: Verification

- [ ] Build the app from the current checkout.
- [ ] Verify the bundled interpreter imports the application and dependencies.
- [ ] Start the bundled backend, fetch `/`, then terminate it cleanly.
- [ ] Run `./scripts/check.sh` and `git diff --check`.
