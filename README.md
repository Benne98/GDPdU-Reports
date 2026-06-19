# GDPdU Reports

Schlanke GDPdU-Edition: **Reporting-Frontend** + **FDD-Bot** (Mathis-Stack) auf GL/GDPdU-Daten.

## Stack (Merge-Dev, Port 5176)

| Dienst | Port | Start |
|--------|------|-------|
| Frontend (Vite `fdd-merge`) | **5176** | `cd frontend && npm run dev:fdd-merge` |
| FastAPI | **8010** | `.\scripts\run-fdd-merge-api.ps1` |
| FDD Rasa REST | **5005** | `.\scripts\run-fdd-merge-rasa.ps1` |
| FDD Rasa Actions | **5055** | `.\scripts\run-fdd-merge-rasa-actions.ps1` |
| PostgreSQL | 5432 | DB `Finssentials` (lokaler Postgres) |

Öffne http://localhost:5176 → Login → `/fdd-bot` → **Upload new data**.

## Erstsetup

```powershell
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:DB_PASSWORD = "<postgres-passwort>"
python scripts\create_database.py
alembic upgrade head
python -m app.seed_admins

# Frontend
cd ..\frontend
npm install
copy .env.fdd-merge.example .env.fdd-merge
```

Rasa-Modelle liegen lokal unter `rasa/models/` (nicht im Repo). Junction oder Kopie aus dem Finssentials-`rasa/models`-Ordner.

## Architektur

| Ordner | Inhalt |
|--------|--------|
| `frontend/` | React/Vite GDPdU UI + FDD-Bot |
| `backend/` | FastAPI, Auth, GL-Compat, FDD-Router |
| `etl/` | GL-Ingestion |
| `rasa/` | FDD-Bot (Rasa 3.x) |
| `*.py` (root) | FDD-Analyse-Skripte (GST, PVM, Databook, …) |
| `scripts/` | Dev-Startskripte + PDF-Deps |

Bewusst **nicht** enthalten: Marketing/Pitch, Expert/Readiness-Bots, Legacy-Pipelines (ARR/Churn/…), Debug-Skripte.
