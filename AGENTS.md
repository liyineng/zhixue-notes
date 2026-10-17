# Project instructions

- This is 智学笔记, a local FastAPI and SQLite course-material study app.
- Preserve existing uploads, notebooks, notes and model settings. Back up the local database before migrations. Keep migrations compatible with existing databases.
- Restrict retrieval to the current notebook and explicitly selected sources. An empty selection must never fall back to other notebooks.
- The user requests a GitHub backup after every completed update. After relevant checks pass, commit and push the finished change to the configured origin. Do not leave a completed update only in the local checkout. If pushing fails, report the failure accurately and retain the local commit.
- Keep databases, uploads, backups, API keys, environment files and personal presentation artifacts out of Git. They are intentionally ignored.
- Ask the user before adding optional new product features. Notebook organization, searching imported sources, saving answers as notes and Markdown export are already authorized.
- Run `.venv/bin/python -m unittest discover -s tests -v` for backend behavior changes. Browser smoke tests are in `scripts/smoke-workspace.mjs`; they use temporary notebooks and mocked model answers and clean up their data.
- Keep vendor licenses intact. Do not rewrite third-party files to resolve formatting-only warnings.
