# FDD Bot — Rasa Project

Conversational agent for the Financial Due Diligence (FDD) workflow.
Replaces the legacy Copilot Studio / Power Automate setup.

## Prerequisites

- Python 3.10
- Rasa 3.6.x (see `requirements.txt`)

## Installation

```bash
cd rasa
pip install -r requirements.txt
```

## Training

```bash
rasa train
```

## Running

Three processes are required (open three terminals):

```bash
# Terminal 1 — FastAPI backend (must already be running)
cd backend
uvicorn main:app --reload --port 8000

# Terminal 2 — Rasa server
cd rasa
rasa run --enable-api --cors "*" --port 5005

# Terminal 3 — Rasa action server
cd rasa
rasa run actions --port 5055
```

## Environment

Set `FASTAPI_BASE_URL` to point to the FastAPI server if it is not on `localhost:8000`:

```bash
FASTAPI_BASE_URL=http://localhost:8000 rasa run actions --port 5055
```

Set `UPLOAD_BASE_DIR` if uploaded files are stored outside the default `uploads/` folder.

## Frontend

The React frontend connects to Rasa via `VITE_RASA_URL` (default: `http://localhost:5005`).
Create `frontend/.env.local`:

```
VITE_API_URL=http://localhost:8000
VITE_RASA_URL=http://localhost:5005
```

## Architecture

```
React FddChatPanel
  → POST /webhooks/rest/webhook (Rasa)
      → Rasa rules + forms
          → Custom actions (rasa-sdk, port 5055)
              → FastAPI /api/v1/fdd/* (port 8000)
                  → Python scripts (GST, PVM, TOP, Databook)
```

## Conversation Topics

| Topic | Rasa Form | FastAPI Endpoint |
|-------|-----------|-----------------|
| Main | `main_form` | `/fdd/upload`, `/fdd/headers`, `/fdd/folders` |
| General Sales Table | `gst_form` | `/fdd/run/gst` |
| PVM Analysis | `pvm_form` | `/fdd/run/pvm` |
| TOP Report | `top_form` | `/fdd/run/top` |
| Apply Filters | `filter_form` | (in-memory, no API call) |
| Reset | rule + intent | — |
| Databook | `databook_form` | `/fdd/run/databook`, `/fdd/run/databook/susa` |

## Extending with NLU

To add intent recognition later, simply:
1. Add training examples to `data/nlu.yml`
2. Add new rules or stories to `data/rules.yml` / `data/stories.yml`
3. Re-run `rasa train`

The DIET classifier pipeline is already configured in `config.yml`.
