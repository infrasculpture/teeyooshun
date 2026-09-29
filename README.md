# teeyooshun
A home-hosted app that gives a Singapore Secondary G3 (Express, SEC pathway, not IP) student tuition-style practice, replacing or reducing paid tuition. Science, Geography and History.

## Deploy and verify on the server

First time only:

```bash
git clone <repo-url> teeyooshun
cd teeyooshun
cp .env.example .env
mkdir -p data
```

Edit `.env` if `PORT` or `DATA_DIR` should differ from the defaults (3002 and `./data`).

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
- `pytest` ends with `1 passed`.
- `http://<server-ip>:3002/` in a browser on the LAN shows "Teeyooshun is running".

If something looks wrong:

```bash
docker compose logs --tail=50 app
```
