# Repository Guidelines

## Project Structure & Module Organization

`starter/` contains the Python application: `agent_interface.py` implements the shopping workflow and deterministic catalog ranking, `api.py` exposes FastAPI routes, and `auth.py`, `store.py`, and `catalog.py` own persistence and domain operations. `frontend/` is the Vue 3 + TypeScript Vite client; place page-level UI in `src/views/`, reusable UI in `src/components/`, and client state in `src/stores/`. `data/` holds the versioned catalog, prompt text, and static images. Keep generated SQLite databases in `local_state/` and do not commit them. Design material and the testing acceptance guide live in `docs/`.

## Build, Test, and Development Commands

Use Python 3.10+ and Node.js 18+.

```powershell
pip install -r requirements.txt
python -m uvicorn starter.api:app --host 127.0.0.1 --port 8000 --reload
cd frontend; npm install; npm run dev
cd frontend; npm run build
```

The backend runs on port 8000; Vite proxies application routes to it. Local test scripts and their configuration are intentionally ignored by Git; use [docs/test-report.md](docs/test-report.md) as the acceptance reference.

## Coding Style & Naming Conventions

Follow the existing Python style: four-space indentation, type annotations where practical, `snake_case` functions/modules, `PascalCase` classes, and concise docstrings for non-obvious behavior. Keep catalog filtering and ranking deterministic; isolate model calls behind `llm_client.py`/the agent interface. In Vue and TypeScript, use two-space indentation, `PascalCase.vue` component filenames (for example, `ProductCard.vue`), and `camelCase` for variables and store actions. No formatter or linter is currently configured, so match nearby code and keep diffs focused.

## Testing Guidelines

Tests are local-only and must not be added to Git. Follow `docs/test-report.md` when running or extending them: use deterministic model doubles where possible, isolate temporary data from `local_state/`, and record real-model evidence without secrets or raw credentials.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit-style prefixes, often with Chinese summaries: `feat: ...`, `fix: ...`, `test: ...`, `docs: ...`, and `refactor: ...`. Keep each commit scoped and imperative. PRs should explain the user-visible change, list verification commands, link relevant issues, and include screenshots for frontend changes. Never commit `.env`, API keys, JWT secrets, or files under `local_state/`.
