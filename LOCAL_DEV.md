# Lokal starten (Mac)

## Einmalig erledigt

- Repo: `/Users/mathi/GDPdU-Reports`
- `backend/.venv` + `npm install` (frontend + root/PM2)
- DB **`Finssentials`** (eigene DB, getrennt von `fissentials`)
- Migrationen + Demo-User (`python -m app.seed_admins`)
- Recon-Mapping-Tabellen: `alembic upgrade head` dann `python backend/scripts/seed_recon_mapping.py` (lädt `Desktop/PL_recon_Mapping.xlsx` und `BS_recon_Mapping.xlsx` in die DB)
- `rasa/models` → Symlink aus Finssentials

## Stack starten

```bash
cd /Users/mathi/GDPdU-Reports
npm run stack:start
```

Stoppen: `npm run stack:stop`  
Logs: `npm run stack:logs`  
Status: `npm run stack:status`

| Dienst | URL |
|--------|-----|
| **App** | http://127.0.0.1:5176 |
| **FDD Bot** | http://127.0.0.1:5176/fdd-bot |
| API | http://127.0.0.1:8010/health |

## Login

- **E-Mail:** `benedikt.hoffarth@finssentials.com`
- **Passwort:** `changeme123`

(Demo-User siehe `backend/app/seed_admins.py`)

## Manuell (ohne PM2)

```bash
bash scripts/run-fdd-merge-api.sh          # :8010
bash scripts/run-fdd-merge-rasa-actions.sh # :5055
bash scripts/run-fdd-merge-rasa.sh         # :5005
cd frontend && npm run dev:fdd-merge       # :5176
```

## Cursor

Workspace öffnen: **File → Open Folder → `/Users/mathi/GDPdU-Reports`**
