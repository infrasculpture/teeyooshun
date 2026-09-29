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

Edit `.env` if `PORT` or `DATA_DIR` should differ from the defaults (3002 and `./data`), and set `ANTHROPIC_API_KEY`. The container runs as user ID 1000, so the data folder must be writable by that user.

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
- `http://<server-ip>:3002/` in a browser on the LAN shows "Teeyooshun is running".

If something looks wrong:

```bash
docker compose logs --tail=50 app
```

## Importing questions

Question files (JSON, format as in `docs/calibration_format_example.json`) live in the data folder, never in git. The file is checked in full first; if anything is wrong, nothing is imported and every problem is listed.

```bash
docker compose run --rm app python -m app.importer data/questions/
```

## Calibration

Marks every test answer in a calibration file with the real model (this costs API credit), prints expected against awarded marks with flags and a summary against the spec's targets, and saves full results to `data/calibration/results/`.

```bash
docker compose run --rm app python -m app.calibrate data/calibration/calibration_set_v1.json
```

Add `--only <question-id>` to mark one question's answers. Re-run whenever the prompts, model or effort change.
