# Finssentials - Architecture Overview

## 1. What is Finssentials?

**Finssentials** is a **web-based financial analysis platform** for M&A and Financial Due Diligence:

- **Reporting** on general-ledger data (P&L, balance sheet, cash flow, working capital, sales)
- **Data ingestion** (Excel/GDPdU upload, mapping, versioning)
- **FDD Bot** — guided workflow with upload, configuration, and Excel outputs
- Optional: additional **chat assistants** (Expert, Exit Readiness)

**Data:** Financial data and client uploads (Excel) — **confidential, GDPR-relevant**. In a later stage also **ERP data** (direct integration).  
**Audience:** Advisory teams and their clients (B2B).

---

## 2. Product variants (please clarify in the call which variant is planned)

| | **Variant A — Full stack** | **Variant B — Lean (GDPdU / FDD)** |
|---|---------------------------|-------------------------------------|
| **Scope** | Cockpit, Financials, Sales, Exit Readiness, FDD Bot | Financials, Data Update, Mapping, FDD Bot |
| **Chatbots** | 3× Rasa (FDD + Expert + Readiness) | 1× Rasa (FDD) |
| **Containers (approx.)** | 9 services + reverse proxy | 5 services + reverse proxy |
| **Typical entry** | Production / larger teams | Pilot / lean rollout |

Both variants share the **same technical foundation** (React, FastAPI, PostgreSQL, Docker).

---

## 3. Architecture (logical)

![Architecture diagram](msp-architecture-diagram.svg)

---

<div class="page-break-before"></div>

## 4. Component checklist

Please quote **staging + production** separately (2× environments).

| # | Component | Requirement | Sizing note |
|---|-----------|-------------|-------------|
| 1 | **PostgreSQL** | Managed, EU (DE/FI/SE), backups, PITR | Core DB; start ~64–256 GB storage |
| 2 | **Application hosting** | Docker Compose or equivalent | Full stack: **8 vCPU / 32 GB RAM**; Lean: **4 vCPU / 16 GB** |
| 3 | **Frontend** | Static SPA (React build) | Same host or CDN |
| 4 | **Reverse proxy / TLS** | HTTPS, routing to API + bots | e.g. Caddy, NGINX, App Gateway |
| 5 | **Object storage** | Uploads & Excel outputs | Start ~100–500 GB per environment |
| 6 | **Identity (SSO)** | OIDC / Entra ID, MFA | For production |
| 7 | **Secrets** | Vault or similar | DB passwords, API keys |
| 8 | **Monitoring & logs** | Health checks, alerts, 30–90 day retention | `/health`, Rasa `/status` |
| 9 | **Backups & restore** | DB + optional blob; **restore test** | GDPR / ISO readiness |
| 10 | **Network** | DB not public; private endpoints | Firewall / VPN for admin |
| 11 | **CI/CD integration** | Pull from **GitHub Container Registry** | No manual FTP deploy |
| 12 | **Optional: AI** | LLM proxy + enterprise endpoint | No raw client data to public APIs |

---

## 5. Rough scale (initial — not maximum)

| Parameter | Planning assumption |
|-----------|---------------------|
| Internal users | 10–20 |
| Clients / engagements | 5–15 (growth expected) |
| Concurrent users | 5–10 |
| FDD sessions / month | ~200–500 |
| Upload size | typically 5–50 MB per file |
| Availability | 99.5%+ (business hours initially OK) |

---

## 6. Compliance & location

| Topic | Requirement |
|-------|-------------|
| **Data residency** | EU (preferably DE, alternatively FI/SE/FR) |
| **DPA** | Required |
| **GDPR** | Client data, deletion & retention concept |
| **ISO 27001** | Path to certification (not required day one) |
| **Sub-processors** | List + transparency |

---

## 7. Five-year scalability plan (Financial Model)

### 7.1 Active customer base (forecast)

| Year (Dec) | **Total** | FDD Bot subscription | Reporting tool | Exit Readiness | All-in-One |
|------------|-----------|----------------------|----------------|----------------|------------|
| **2026** | **9** | 3 | 6 | 0 | 0 |
| **2027** | **61** | 18 | 18 | 12 | 13 |
| **2028** | **132** | 39 | 33 | 30 | 31 |
| **2029** | **235** | 71 | 51 | 56 | 57 |
| **2030** | **388** | 120 | 73 | 97 | 98 |

### 7.2 Infrastructure scaling requirements (derived)

| Year (Dec) | Active customers | Tenants with GL data¹ | Peak concurrent users² | FDD sessions / month³ | PostgreSQL (prod)⁴ | Object storage (prod)⁴ | Application tier⁵ |
|------------|------------------|------------------------|------------------------|-------------------------|--------------------|-------------------------|-------------------|
| **2026** | 9 | 6 | 6 | ~15 | 64 GB | ~200 GB | **4 vCPU / 16 GB** |
| **2027** | 61 | 43 | 40 | ~125 | 200 GB | ~1.2 TB | **8 vCPU / 32 GB** |
| **2028** | 132 | 93 | 80 | ~280 | 400 GB | ~2.5 TB | **8 vCPU / 32 GB** (+ read replica optional) |
| **2029** | 235 | 164 | 140 | ~510 | 700 GB | ~4.5 TB | **16 vCPU / 64 GB** or 2× app nodes |
| **2030** | 388 | 268 | 235 | ~870 | **1–1.5 TB** | **~7 TB** | **2× app nodes** (8 vCPU / 32 GB each) + DB replica |

**Planning assumptions:**

1. **Tenants with GL data** = Reporting + Exit Readiness + All-in-One (products that store and derive general-ledger data).  
2. **Peak concurrent users** ≈ 4 named users per customer × 15 % concurrent peak (advisory + client users).  
3. **FDD sessions / month** ≈ 4 runs per month per FDD Bot subscription and All-in-One customer.  
4. **Storage** = PostgreSQL incl. derived KPI tables & snapshots; object storage = uploads + Excel outputs (~8 GB/customer/year + ~15 GB/GL tenant). Rounded; quote with growth headroom (+30 %).  
5. **Application tier** = combined API + Rasa + actions host(s); frontend static/CDN separate.
