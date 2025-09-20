# Repository Guidelines

## Project Structure & Module Organization
The Chrome extension lives in `extension/` with a Manifest V3 setup (`manifest.json`, `content.js`, `sw.js`). The FastAPI backend is in `server/`, split into orchestrating logic (`main.py`), OCR helpers (`ocr.py`), grouping heuristics (`grouping.py`), and translation adapters (`translate.py`). Shared Python dependencies are pinned in `requirements.txt`. Assets are fetched at runtime, so there is no dedicated static directory yet; add new artifacts under the nearest feature folder to keep the tree shallow.

## Build, Test, and Development Commands
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server.main:app --reload
```
The first command creates an isolated environment; the second installs required and optional backend packages. `uvicorn` starts the API at `http://localhost:8000`, which the extension expects. Load the extension in Chrome via `chrome://extensions` → **Load unpacked** → `extension/`. During manual testing, hit `GET /health` to confirm the server is ready before launching translation flows.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indents and type hints for Python modules; prefer descriptive snake_case function names (e.g., `translate_groups_jp_to_en`). Existing files consistently use double quotes and module-level logging—match that tone. JavaScript in `extension/` uses `const`/`let`, camelCase helpers, and template literals for styles; keep indentation at two spaces and avoid mutating global state outside `IMAGE_STATE` and DOM helpers. Document non-obvious heuristics with brief comments.

## Testing Guidelines
Automated tests are not yet present, so start new suites under `server/tests/` using `pytest` if you add coverage. For now, exercise `POST /analyze` manually with `curl` or a REST client using real manga images, and verify overlays render in popular aspect ratios. Add regression fixtures when introducing new OCR or translation logic to avoid breaking existing group ordering.

## Commit & Pull Request Guidelines
Current commits are concise imperative phrases (e.g., "scuffed v1"); continue using short lowercase summaries that describe intent. Reference related specs or issues in the body, and call out impacts on extension UX or backend contracts. PRs should include: purpose, screenshots or screencasts when UI changes occur, manual test notes (server + extension), and any required environment variables so reviewers can reproduce locally before merging.

## Security & Configuration Tips
Never embed API keys or service-account JSON in the extension bundle; instead, rely on environment variables such as `GOOGLE_APPLICATION_CREDENTIALS` and `CEREBRAS_API_KEY` when the backend boots. Treat translated content caches as transient—clear them before committing to avoid leaking user material.
