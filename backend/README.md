# backend — KasMap API

FastAPI app deployed to **Vercel** (project Root Directory = `backend`).

| Endpoint | |
|---|---|
| `GET /health` | liveness |
| `GET /v1/status` | coverage from the database (`KASMAP_DATABASE_URL` or `DATABASE_URL`) |
| `GET /docs` | OpenAPI UI |

Local: `pip install -r backend/requirements.txt fastapi[standard] && fastapi dev backend/app.py`
