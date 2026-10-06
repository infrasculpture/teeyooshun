# teeyooshun
A home-hosted app that gives a Singapore Secondary G3 (Express, SEC pathway, not IP) student tuition-style practice, replacing or reducing paid tuition. Science, Geography and History.

## Deploy and verify on the server

First time only:

```bash
git clone <repo-url> teeyooshun
cd teeyooshun
cp .env.example .env
mkdir -p data
sudo chown -R 1000:1000 data
```

Edit `.env` if `PORT` or `DATA_DIR` should differ from the defaults (3002 and `./data`), and set:

- `ANTHROPIC_API_KEY`
- `PARENT_PIN`: at least 6 digits; parent features stay off while it is blank or `0000`
- `SESSION_SECRET`: a long random string, e.g. the output of `openssl rand -hex 32`

The container runs as user ID 1000, so the data folder must be writable by that user.

Every deploy, from the `teeyooshun` folder:

```bash
git pull
docker compose up -d --build
docker compose ps
curl -s http://localhost:3002/health
docker compose run --rm app pytest
```

What a good result looks like:

- `docker compose ps` shows the `app` container as `Up ... (healthy)`. It shows `(health: starting)` for up to about 30 seconds after a deploy.
- `curl` prints `{"status":"ok"}`.
- `pytest` ends with `N passed` and no failures.
- `http://<server-ip>:3002/` in a browser on the LAN shows the Practice page with a subject button for each subject in the question bank.

If something looks wrong:

```bash
docker compose logs --tail=50 app
```

## Importing questions

Question files (JSON, format as in `docs/calibration_format_example.json`) live in the data folder, never in git. Each file is checked in full first; if anything is wrong, nothing from that file is imported and every problem is listed. Any `answers` in the file (calibration test answers) are never stored.

```bash
docker compose run --rm app python -m app.importer data/questions/
```

Questions are matched by `id`, so re-running an import never duplicates them. An edited question gets the next version number; attempts she has already made keep pointing at the version she answered. Calibration files import into the same question bank, so calibration questions are available for practice too.

## Ingesting scanned books and papers

Spec: `docs/INGESTION_SPEC.md`. Put each source in its own folder in the data directory, with a `source.yaml` (templates in `docs/templates/`):

```
data/sources/<subject>/<level>/<slug>/source.yaml + PDFs
```

After copying files in, make sure the container user can read them: `sudo chown -R 1000:1000 data`.

```bash
docker compose run --rm app python -m app.ingest data/sources/science/sec1/mc-lss-assessment --dry-run
docker compose run --rm app python -m app.ingest data/sources/science/sec1/mc-lss-assessment
```

- `--dry-run` counts pages and estimates cost without calling the API. `--file qns_c01.pdf` processes one PDF.
- Each page is rendered to `data/renders/` and read by `INGEST_MODEL`. Pages with a name, class or index box are skipped and their render deleted; nothing from them is kept.
- Each question page also gets a sharper display image (`data/renders/<slug>/<file>-vN/display/`, long edge `DISPLAY_MAX_EDGE`, default 2400 px). The question screen shows the page images for the whole question above the typing box; answer pages are never shown, and worked examples are left out of practice. Only questions answered by drawing or labelling a diagram are left out of typed practice. For a question that depends on a figure, the page image is also sent with the marking call.
- Re-running is safe: unchanged files are skipped, a changed file becomes a new version, and failed pages are retried.
- `--file NAME --pages 18-20` reads only those pages again with the current instructions (e.g. after a prompt fix). The file keeps its version, the other pages keep their stored readings, and the items are rebuilt; card IDs stay the same for rows whose prompts haven't changed. Pending edits on that file are replaced.
- `--force` reads the selected files again as new versions even if unchanged (this run only; combine with `--file` to limit it). Every page is sent to the model again, so it costs the same as a first run. Pending items from the old version, including saved edits, are superseded; approved items stay in the question bank.
- The run ends with a summary per file: pages, tokens and estimated cost, item counts, warnings grouped by type, questions without an answer, answers without a question, and pages not read with the model's reason. Show it again at any time (no API calls):

```bash
docker compose run --rm app python -m app.ingest.summary data/sources/science/sec1/mc-lss-assessment --file qns_c01.pdf
```

- Exam papers with sections: question numbers keep their section letter (`B5`). If the paper and its key number a section differently (paper `C12, C13`; key `C1, C2`) and both have the same questions and parts in order, they are matched by position with a "confirm" warning, and the summary lists the mapping. For anything that can't be aligned, add `numbering_overrides` to `source.yaml` (see `docs/templates/source_papers.yaml`) and reassemble.
- Worked-example solutions inside a questions file are read as answers for the questions before them, and take priority over the book's answer key for those questions.
- After a code fix, rebuild staged items from the stored page output without calling the API. It previews the changes by default and only saves them with `--apply`. Pending items (and their saved edits) are replaced; approved items stay:

```bash
docker compose run --rm app python -m app.ingest.reassemble data/sources/science/sec1/mc-lss-assessment --file qns_c01.pdf
```
- Nothing reaches the question bank until a parent approves it at `http://<server-ip>:3002/review` (parent PIN). Approving an item that already exists creates its next version.
- Handles `questions`, `answer_key`, `exam_paper_with_key` and `revision_tables`.
- Revision tables (`rev_u01.pdf`) become revision cards, one per table row, staged for review like questions and stored in `revision_cards` when approved (the Revise screen that uses them is a later task). A row continuing onto the next page joins its card; an answer of more than 8 points is split along its own groups or sub-points, and a long flat list is kept whole with a warning. Card IDs stay the same across re-reads and edits. The summary shows the rows the model counted against the cards made.

## Multiple-choice questions

MCQs are marked by code, with no API call. After she answers, a short explanation from `LIGHT_MODEL` says why the correct option is right and, if she was wrong, why her option is wrong and what misconception it suggests. It is written once per question version and chosen option, stored in the database and reused after that; the page image is included when the question depends on a diagram. If the call fails she still sees her result and the correct answer, with a "Try again" link for the explanation.

## Parent actions

On any feedback page, tap **Parent** and enter the PIN. For 12 hours on that device you can agree or disagree with a marking (with an optional note) and see the confidence reason and flags. After 5 wrong PINs, parent login locks for 5 minutes. Changing `PARENT_PIN` or `SESSION_SECRET` logs every device out. The student can press "This marking looks wrong" herself; parents see those flags in the parent panel.

## Calibration

Marks every test answer in a calibration file with the real model (this costs API credit), prints expected against awarded marks with flags and a summary against the spec's targets, and saves full results to `data/calibration/results/`.

```bash
docker compose run --rm app python -m app.calibrate data/calibration/calibration_set_v1.json
```

Add `--only <question-id>` to mark one question's answers. Re-run whenever the prompts, model or effort change.
