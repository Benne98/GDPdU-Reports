# P1 — GL-Ingestion über das Berater-Fenster (Consultant Mapping Window)

> **Status:** Entwurf v0.1 · gehört zu [`PLAN.md`](./PLAN.md) (Phase P1) · Schema: [`../db/gl-target-structure.md`](../db/gl-target-structure.md)
> **Datenschutz:** Echte Mandantendaten (z. B. `decidra/*`) werden **nie** committet/geloggt (`.gitignore`); Tests laufen ausschließlich mit **synthetischen Fixtures**. Die Pipeline ist **generisch** — keine mandantenspezifischen Spalten hartkodiert.

---

## 1. Ziel
Ein Berater lädt eine **GL-Buchungszeilen-Datei** (CSV/XLSX/Parquet) und die **Account-Mapping-Datei** hoch und ordnet die **Quellspalten per Drag & Drop** unseren **kanonischen Zielfeldern** zu. Danach laufen **Validierungs-Checks**; nur bei Bestehen werden die Daten in die kanonische Struktur (`fact_gl_entry/line`, `dim_gl_account/na/cf`, abgeleitete Facts) geladen.

Designziel: **Self-service** für Mitarbeiter, **wiederverwendbare Mapping-Profile** je Mandant/Quellsystem, **harte Qualitäts-Gates** vor dem Laden.

---

## 2. UX-Flow (Wizard, 6 Schritte)
Zugriff: Berater/Admin (Auth, §P4). Seite „Ingestion" (bzw. integriert in Role management für Admins).

1. **Upload** — Datei(en) per Drag & Drop. Backend erkennt Format, Encoding, Trennzeichen, Dezimal-/Tausender-Zeichen, Sheets (xlsx); liefert **Header + Beispielzeilen** (Preview) zurück.
2. **Kontext** — Zuordnung zu **Entity** und **Geschäftsjahr**: entweder feste Auswahl (eine Datei = eine Entity/ein Jahr) oder Mapping einer Entity-/Jahres-Spalte. Vergabe des **2-stelligen `entity_prefix`** (aus `dim_legal_entity`).
3. **Spalten-Mapping (Drag & Drop)** — Quellspalten → Zielfelder (§3, §4). Pflichtfelder markiert; Auto-Vorschläge per Namensähnlichkeit; Speicherung als **Mapping-Profil**.
4. **Transform-Optionen** — pro Feld: Datumsformat, Dezimal-/Vorzeichen-Logik (ein signierter Betrag vs. Soll/Haben-Spalten vs. Betrag + S/H-Kennzeichen), Konto-Normalisierung (`.0` strippen, zero-pad). **Linking-Strategie** für Debitoren/Kreditoren (txn-Propagation vs. Konto/Gegenkonto, siehe Schema §12).
5. **Validierung** — Checks (§6) laufen auf **Staging** (kein DB-Write); Ergebnis als Pass/Fail-Report mit **Klick in die fehlerhaften Zeilen**.
6. **Commit** — bei Pass (bzw. Override bei Soft-Warnungen) Transform + Load in die kanonische Struktur; Eintrag in `meta_dataset_load` (Dedup). Bei Hard-Fail: Blockade + herunterladbarer Fehlerreport.

---

## 3. Ziel-Feld-Manifest (Mapping-Ziele)
Das Manifest treibt sowohl die Mapping-UI als auch die Validierung. `req` = Pflicht.

### 3.1 Buchungszeilen-Datei → Entry-Header + Line
| Zielfeld | Ziel-Tabelle | req | Typ | Transform/Hinweis |
|----------|--------------|-----|-----|-------------------|
| entity | (Schlüsselbau) | ✔ | code/spalte | feste Auswahl **oder** Spalte → `entity_prefix` |
| journal_entry_number | entry/line | ✔ | string | → Teil von `journal_entry_group_number` |
| posting_date | entry | ✔ | date | Datumsformat wählbar |
| fiscal_year | entry/line | ✔ | int | Spalte **oder** aus `posting_date` |
| fiscal_period | entry | – | int | aus `posting_date` ableitbar (13 = Konsolidierung) |
| document_date | entry | – | date | |
| document_type | entry | – | string | Code/Text → `dim_document_type` |
| reference_document_number | entry | – | string | Belegnummer |
| currency | entry | – | string | Default EUR |
| header_note / line_note | entry/line | – | string | |
| account_number | line | ✔ | string | normalisieren → Teil von `account_number_group` |
| amount | line | ✔ | decimal (signiert) | **Vorzeichen-Logik** (§4) |
| vat_amount | line | – | decimal | |
| source_type | (Ableitung) | – | string | „Debitor"/„Kreditor" → customer/supplier |
| source_no | (Ableitung) | – | string | Debitoren-/Kreditorennummer |
| posting_type | line | – | string | |

### 3.2 Account-Mapping-Datei → `dim_gl_account` (+ `dim_gl_na`, `dim_gl_cf`)
| Zielfeld | Ziel | req | Hinweis |
|----------|------|-----|---------|
| account_number | dim_gl_account | ✔ | Join-Basis → `account_number_group` (mit entity_prefix) |
| level_0 (BS/PL) | dim_gl_account | ✔ | ersetzt `statement_type` |
| level_1..4, l4_sub | dim_gl_account | ✔ | Hierarchie |
| level_2_sort, level_3_sort | dim_gl_account | – | Sortierung |
| is_ic (L10) | dim_gl_account | – | IC-Flag |
| l6_na_mapping, l7_na_description | dim_gl_na | – | Working Capital |
| cf l1..l5, cf_mapping | dim_gl_cf | – | Cashflow |

> Mapping je **Entity & Jahr** (Schema §8.4). Mehrere Mapping-Dateien (je Entity) + eine für die konsolidierte Gruppe möglich.

---

## 4. Spalten-Mapping per Drag & Drop (UI)
- **Zwei-Panel-Layout:** links **Quellspalten** als Chips (mit 2–3 Beispielwerten), rechts **Zielfelder** als Drop-Zonen, gruppiert (Entry-Header / Line / Partner / Mapping-Hierarchie).
- **Interaktion:** Chip auf Zielfeld ziehen = Zuordnung. Mehrfach-Quellen (z. B. Soll + Haben → ein `amount`) erlaubt über eine „Kombinieren"-Drop-Zone.
- **Hilfen:** Auto-Match per Namensähnlichkeit (Vorbelegung, bestätigbar); Pflichtfelder rot bis belegt; „unmapped Pflichtfeld" blockiert Weiter.
- **Profile:** „Als Profil speichern" je `source_system`/Mandant; beim nächsten Upload automatisch vorgeschlagen.
- **Wiederverwendung:** an die bestehenden Muster `fdd-bot/SusaColumnMapper` / `AdaptiveCard` angelehnt, aber als dedizierte, schlanke GL-Mapper-Komponente.

### 4.1 Vorzeichen-/Betragslogik (wählbar)
| Modus | Quelle | Ergebnis |
|-------|--------|----------|
| signed | eine Betragsspalte (bereits signiert) | direkt `amount` |
| soll_haben | zwei Spalten (Soll, Haben) | `amount = Soll − Haben` (DATEV) |
| amount_dc | Betrag + S/H-Kennzeichen | `amount = ±|Betrag|` |

### 4.2 Schlüsselbau (deterministisch, durch die Pipeline)
- `entity_prefix` (2-stellig) aus Kontext/Spalte.
- `account_number_group = entity_prefix ⊕ zero-pad(account_number)` (8-stellig).
- `journal_entry_group_number = entity_prefix ⊕ zero-pad(journal_entry_number)` (12).
- `customer_id/supplier_id = entity_prefix ⊕ source_no` (bei Debitor/Kreditor).
- Preview der konstruierten Schlüssel vor dem Commit.

---

## 5. Linking Debitoren/Kreditoren → Sales/CoM
Wählbare **Linking-Strategie** (Schema §12.2/§12.3):
- **A txn-Propagation (GoBD):** Partnernummer von der AR/AP-Zeile auf Umsatz-/Materialzeilen derselben `journal_entry_group_number` propagieren.
- **B Konto/Gegenkonto (DATEV):** Partner und GuV-Konto aus einer Zeile.
- **none:** keine Partnerzuordnung (nur Beträge).
Der Berater bestätigt im Wizard, in welcher Spalte die Debitoren-/Kreditorennummer liegt und ob sie bei den Sales/Expense-Buchungen verknüpfbar ist.

---

## 6. Check-Katalog (detailliert)
Severity: **HARD** = blockiert Commit · **SOFT** = Warnung (Override möglich). Jeder Check liefert: Status, betroffene Zeilen/Gruppen, Summenabweichung.

### 6.1 Strukturell
| ID | Regel | Severity |
|----|-------|----------|
| S1 | Alle Pflichtfelder gemappt & nicht-null | HARD |
| S2 | `posting_date` parsebar; Jahr passt zu `fiscal_year` (Ausnahme Eröffnungsbuchungen) | HARD |
| S3 | `amount` numerisch; Dezimal/Vorzeichen korrekt geparst | HARD |
| S4 | `entity_prefix` genau 2-stellig; `account_number_group`/`journal_entry_group_number` formatkonform | HARD |
| S5 | `fiscal_period` ∈ 1..13 | HARD |

### 6.2 Bilanzielle Balance (doppelte Buchführung)
| ID | Regel | Severity |
|----|-------|----------|
| **B1** | **Σ `amount` pro `(journal_entry_group_number, fiscal_year)` = 0** (Tol. 0,01) | HARD |
| **B2** | **Σ `amount` über alle Zeilen je Entity/Jahr = 0** (Gesamt-Soll = Gesamt-Haben) | HARD |
| B3 | **Trial Balance:** Σ je Konto aufsummiert über alle Konten = 0 | HARD |
| B4 | BS-Artikulation: Σ BS-Konten (Aktiva − Passiva) = 0 je Stichtag | HARD |
| B5 | P&L↔BS: Jahresergebnis (P&L) = Veränderung Gewinnvortrag/EK (BS) | SOFT |

### 6.3 Mapping-Abdeckung
| ID | Regel | Severity |
|----|-------|----------|
| M1 | Jede `account_number` im GL existiert im Account-Mapping | **SOFT** — Warnung, Stub anlegen, durchführen; danach manueller Mapping-Schritt (§6.6) |
| M2 | Jedes Konto hat `level_0` (BS/PL) und vollständige Hierarchie `level_1..4` | HARD |
| M3 | WC-relevante Konten haben `l6_na_mapping`; CF-relevante haben `cf_*` | SOFT |
| M4 | Mapping je (Entity, Jahr) vorhanden für alle vorkommenden (Entity, Jahr) | HARD |

### 6.4 Reconciliation Debitoren/Kreditoren (Kern deiner Vorgabe)
| ID | Regel | Severity |
|----|-------|----------|
| **R1** | **Σ AR (Debitorenzeilen) = Σ GL-Saldo Trade-receivables-Konten** je Entity/Stichtag | HARD |
| **R2** | **Σ AP (Kreditorenzeilen) = Σ GL-Saldo Trade-payables-Konten** | HARD |
| **R3** | **Σ `gross_sales` (fact_sales) = Σ Umsatzkonten** (`−amount` auf Revenue) | HARD |
| **R4** | **Σ `cost_of_materials` (fact_com) = Σ Materialkonten** | HARD |
| R5 | Partner-Link-Abdeckung: Anteil Umsatz-/Materialzeilen mit Kunde/Lieferant; unverlinkte ausweisen | SOFT |
| R6 | Jede Debitor-/Kreditor-Zeile hat eine gültige `source_no` (sonst Partner = unbekannt) | SOFT |

### 6.5 Qualität & Dedup
| ID | Regel | Severity |
|----|-------|----------|
| **Q1** | **Dedup:** `content_hash` + (Entity, Jahr, Periode) gegen `meta_dataset_load`; bereits geladene Daten **nicht** erneut laden, nur Neues ergänzen | HARD (nur bei `commit_mode=append`) |
| Q2 | `booking_line_id` / `(jegn, fiscal_year, line_number)` eindeutig | HARD |
| Q3 | Header-Felder je `journal_entry_number`-Gruppe konsistent (posting_date etc.) | SOFT |
| Q4 | Währungskonsistenz; VAT-Plausibilität | SOFT |
| Q5 | Ausreißer/Nullbeträge/Dubletten-Belege ausweisen | SOFT |

> Der Report zeigt je Check: betroffene Gruppen, Summenabweichung, Beispielzeilen, und (bei B-/R-Checks) den genauen Differenzbetrag.

### 6.6 Unmapped-Konten-Auflösung (manueller Mapping-Schritt)
Wenn M1 anschlägt (Konten ohne Mapping): **kein Block**, sondern
1. Stub-Zeilen in `dim_gl_account` (mit `is_unmapped`-Kennzeichen) anlegen, Ingestion läuft weiter (SOFT).
2. Ein **Auflösungs-Screen** listet die unmapped Konten; der Berater weist je Konto `level_0..4`, `l4_sub`, Flags (is_ic) und ggf. NA/CF zu (Drag&Drop/Auswahl) → Persistenz nach `dim_gl_account`/`dim_gl_na`/`dim_gl_cf`.
3. Betroffene Checks (M2, B3, R1–R4) werden danach erneut gerechnet.
Diese manuelle Zuordnung ist ebenfalls als **Profil** speicherbar (Konto → Mapping), damit Folge-Uploads sie automatisch übernehmen.

---

## 7. Pipeline-Architektur

### 7.1 Backend-Endpunkte (neues Repo, FastAPI)
| Endpoint | Zweck |
|----------|-------|
| `POST /api/v1/ingest/upload` | Datei speichern (`uploads/`), Format/Dialekt erkennen, Header + Sample + Sheets zurück |
| `POST /api/v1/ingest/profiles` · `GET …` | Mapping-Profile speichern/laden (je Mandant/Quellsystem) |
| `POST /api/v1/ingest/validate` | Mapping + file_id → Checks auf **Staging**, Report (kein DB-Write) |
| `POST /api/v1/ingest/commit` | bei Pass: Transform + Load kanonisch + `meta_dataset_load` (`commit_mode`: default `replace` = Scope-Replace; `append` = Delta-Top-up mit Dedup) |
| `GET /api/v1/ingest/versions` · `GET …/{load_id}` | Version-Historie je `load_id` (Scope, Modus, Snapshot-Status) |
| `POST /api/v1/ingest/versions/{load_id}/restore` | Admin: Scope auf Post-Commit-Stand des gewählten Loads zurücksetzen |
| `GET /api/v1/ingest/runs` | Alias der Version-Historie (ohne Restore-Audit-Einträge) |

### 7.2 ETL-Modul (`etl/`, reine Funktionen, testbar)
- `dialect.py` — Encoding/Trennzeichen/Dezimal-Erkennung.
- `mapping.py` — Manifest, Profil-Anwendung, Auto-Match.
- `transform.py` — Typ-/Locale-Normalisierung, Vorzeichenlogik, Schlüsselbau.
- `derive.py` — abgeleitete Facts (sales/com/ar/ap), Linking-Strategien A/B.
- `checks.py` — **jeder Check eine Funktion** `-> CheckResult{id, severity, passed, detail, rows}`; ein Runner aggregiert.
- `load.py` — transaktionaler Load Staging → canonical; Dedup via `meta_dataset_load` (append); Scope-Replace + Snapshot via `versioning.py`.

### 7.4 Data Versioning (load_id-Restore)
- Jeder erfolgreiche GL- oder Mapping-Commit erzeugt einen **Post-Commit-Snapshot** (`snap_fact_gl_*`, `snap_dim_gl_*`) getaggt mit `load_id`.
- **Restore** löscht den betroffenen Scope in den Live-Tabellen, spielt den Snapshot zurück und leitet abgeleitete Facts (AR/AP/Sales/CoM) neu.
- GL-Default: `commit_mode=replace` (Scope-Replace). `append` für echte Delta-Dateien; Q1-Dedup nur bei `append`.
- Retention aller Snapshots im MVP; `SNAPSHOT_RETENTION_MAX` als Follow-up.

### 7.3 Staging & Commit
- Upload → DataFrame/Staging; Checks laufen darauf. **Erst bei Pass** transaktionaler Write nach canonical (alles-oder-nichts pro Ingestion-Run).
- `meta_dataset_load` protokolliert (dataset, entity, year, period, row_count, content_hash, loaded_by) → Dedup + späterer inkrementeller Top-up (Schema §9 PLAN).

---

## 8. Frontend-Komponenten (neues Repo)
- **`IngestionWizard`** (Seite, berater/admin-only): Schritte Upload → Kontext → Mapping → Validierung → Commit.
- **`ColumnMapper`** (Drag & Drop, zwei Panel) + `MappingProfileBar`.
- **`ValidationReport`** (Check-Liste mit Drill-in zu Fehlzeilen) + `KeyPreview`.
- Wiederverwendung: Upload/Adaptive-Card-Muster aus `fdd-bot/*`; Tabellen/Drill aus dem Shared-Layer.

---

## 9. Tests (nur synthetisch — CLAUDE.md)
- **Fixtures:** generierte GoBD-/DATEV-ähnliche Mini-Datensätze (balanciert & bewusst unbalanciert) + Mini-Account-Mapping. **Keine** echten Mandantendateien.
- Golden-File-Tests für `transform`/`derive` (Schlüsselbau, Vorzeichen, Linking A/B).
- Unit-Tests je Check (S/B/M/R/Q) — positiv & negativ.
- Reconciliation-Tests R1–R4 mit konstruierten Salden.
- Property/Edge-Cases: Sammelbuchungen (mehrere Debitoren), Barverkauf (kein Partner), Storno/Gutschrift, Eröffnungsbilanz, USt-Zeilen nicht als Sales.

---

## 10. Implementierungs-Teilphasen
| Teil | Inhalt |
|------|--------|
| **P1a** | `ingest/upload` + Dialekt-/Header-Erkennung (Backend) |
| **P1b** | Manifest + `transform`/Schlüsselbau (reine Fns + synthetische Tests) |
| **P1c** | `checks.py` Check-Katalog (reine Fns + Tests) |
| **P1d** | `IngestionWizard` + `ColumnMapper` (Frontend) |
| **P1e** | `derive` (sales/com/ar/ap) + Reconciliation-Checks R1–R4 |
| **P1f** | `commit`/Load + `meta_dataset_load` + Dedup + Mapping-Datei-Ingestion |

Jede Teilphase: kleiner Diff, Tests grün, Quality-Gate.

---

## 11. Entscheidungen (erledigt)
1. ✅ **Staging:** **in-memory** (DataFrame) für jetzt (decidra-GoBD ~24k Zeilen). **Umstieg auf physische Staging-Tabellen + `COPY` + SQL-Checks** ab ~mehreren 100k Zeilen / SaaS-Mehrlast.
2. ✅ **Unmapped-Konten:** **SOFT** (Warnung, Stub, durchführen) + **manueller Mapping-Schritt** (§6.6), als Profil wiederverwendbar.
3. ✅ **Fachliches Konten-Mapping** liegt als **`dim_gl_account`** (+ `dim_gl_na`/`dim_gl_cf`) in der DB (Name beibehalten; *ist* das Konten-Mapping).
4. ✅ **Technisches Spalten-Mapping-Profil:** neue Tabelle **`ingest_mapping_profile`** (JSON je Mandant/Quellsystem) — speichert Quellspalte→Zielfeld + Transform-Optionen + Konto-Auflösungen.

## 12. Noch offen
- **R-Check-Toleranz:** absolut (0,01) und/oder relative Schwelle? Umgang mit USt-Rundungsdifferenzen. *(Default vorerst: absolut 0,01; relativ 0,1 % als Soft-Schwelle.)*
- **Namens-Bestätigung:** `dim_gl_account` beibehalten (empfohlen) vs. Umbenennung in `dim_account_mapping`?
