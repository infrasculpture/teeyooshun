# Teeyooshun: instructions for Claude Code

Teeyooshun is a home-hosted tutoring app for one Singapore Secondary G3 (Express, SEC) student: syllabus-aligned practice questions, detailed marking that shows where every mark comes from, weak-topic targeting, and a parent dashboard. Science first; History and Geography later. The goal is to raise her marks from about 45% to about 75%. Good enough beats perfect.

Read `docs/PROJECT_BRIEF.md` before any substantial task. Its Requirements, Constraints and Decisions log are settled. `docs/SYLLABUS_TOPIC_MAP.md` is the topic backbone.

## How James works (read this first)

- James is an experienced Linux sysadmin who reads code but is not a developer. Explain in plain terms.
- **Nothing can run on this laptop.** There is no Python, Docker or test environment here, and none should be installed. Write code carefully and say how James can verify it on the server.
- Workflow: you edit files here → James reviews the diff → James commits with GitHub Desktop and pushes → James pulls on the Ubuntu server and runs it in Docker → James reports errors back to you.
- **Never run `git commit`, `git push` or anything that rewrites history.** James commits.
- End every task with: (1) a short summary of what changed; (2) the exact server commands to deploy and verify, with what a good result looks like; (3) a suggested commit message.
- Work in small steps. Ask before large changes, new dependencies or new files outside the agreed structure.
- Stay inside the brief. Label anything beyond it **"Beyond scope"** and wait for James to accept or reject it.

## Hard rules

- **No secrets in the repo.** API keys and passwords go only in `.env` on the server; commit `.env.example` with placeholder values.
- **No source material in the repo.** Scans, textbook text, exam papers, extracted question banks and the database live in the server's data directory (mounted into the container), never in git. They are copyrighted and for home use only.
- **No personal data about the student in code, fixtures or tests.** Use made-up examples.
- Never read or edit files outside this repository folder.
- LF line endings everywhere (see `.gitattributes`); shell scripts must run on Linux.

## Stack and deployment

- Python 3.12, FastAPI, server-rendered Jinja2 templates with htmx for interactivity (no JavaScript build step), SQLite, the official `anthropic` Python SDK. Keep dependencies few and pinned in `requirements.txt`.
- Docker: one `Dockerfile` (python slim base) and one `compose.yaml`, following James's existing convention:
  - `build: .`
  - host port `${PORT:-3002}` mapped to the container's internal port
  - `env_file: .env`
  - data directory bind-mounted from `${DATA_DIR:-./data}` to `/app/data`
  - `restart: unless-stopped`
  - an HTTP healthcheck written in Python (the slim image has no `wget` or `curl`)
- Server: Ubuntu 26.04 LTS, Docker 29, Compose v5, x86_64. It is a production and backup server: nothing runs there except the container. Plain HTTP on the LAN by port; remote access via Tailscale. No reverse proxy.
- Tests use `pytest` and run on the server inside the container, e.g. `docker compose run --rm app pytest`.

## Architecture principles

- The workflow lives in code. Claude (via the API) is used only for bounded tasks: marking an answer against a stored mark scheme, generating a question, explaining a concept, giving a hint.
- **All scoring happens on the server.** The mark scheme never reaches the browser before the student has submitted her answer. The student UI can submit answers but cannot edit or delete history.
- Keep API prompts small and cache stable parts (prompt caching). Model names are configuration, not hard-coded: a smaller model for hints and simple explanations, a larger one for multi-mark and level-of-response marking.
- Every question and attempt is tagged with subject, level (Sec 1–4) and topic. Subjects and levels are data, not hard-coded.
- Parent pages sit behind a simple PIN (set in `.env`). No full user accounts.
