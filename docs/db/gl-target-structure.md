# GL-Zielstruktur — Canonical Datenmodell (General Ledger)

> **Status:** Entwurf v0.5 · **Scope:** General Ledger (fact + dim) + abgeleitete Fact-Tables (AR/AP/Sales/CoM) · **Autor:** Backend (Claude) auf Basis der Anregungen von Benedikt
> **Speicherort:** `docs/db/gl-target-structure.md` · Migrationspfad: `docs/db/gl-migration-plan.md`. Reines **Design-Dokument**.
> **v0.5:** Abgeleitete Fact-Tables `fact_ar`/`fact_ap`/`fact_sales`/`fact_com` ergänzt (§12) — inkl. der zwei Verknüpfungsmethoden GoBD-Propagation vs. DATEV Konto/Gegenkonto.
> **v0.6:** Partner- & Geo-Dimensionen ergänzt (§13): `dim_customer`/`dim_supplier` (1:1, entity-aware) + Geografie-Snowflake `dim_country`/`dim_region` mit ausgeschriebenen Namen für mehrstufige Aggregation.
> **v0.7:** Plan-/Forecast-Tabellen ergänzt (§14): `fact_gl_plan` (deckt P&L/BS/CF ab) + `fact_sales_plan` (nach Kunden); Szenario-Modell FY25F/FY26P–FY29P, zunächst synthetisch, im Table View editierbar.
>
> **v0.2:** Schlüsseldesign auf die minimale, entity-bewusste Variante umgestellt. `legal_entity_code` als Spalte entfällt; Entity in zusammengesetzte Schlüssel eingebettet. Verifiziert gegen Echtdaten (§3.0).
> **v0.3-Entscheidungen:** (1) Entity-Prefix **2-stellig** (Skalierung bis 99 Entities). (2) Mapping-Versionierung allein über **`fiscal_year`** (kein `valid_from/valid_to`). (3) **`statement_type` entfällt** — durch `level_0` ersetzt (in Echtdaten wertgleich, §3.0).
> **v0.4-Entscheidungen:** (1) NA/CF-Mapping **nicht zwingend konstant** über Jahre → `fiscal_year` bleibt im PK von `dim_gl_na`/`dim_gl_cf`. (2) Sortierung **bleibt** über `level_2_sort`/`level_3_sort`. (3) `cf_mapping` wird **gespeichert** (Spalte in `dim_gl_cf`).

---

## 1. Zweck & Geltungsbereich

Zielstruktur der GL-Tabellen (Felder, Joins, Views) als Basis-Modul. Alle weiteren Module (Sales, Opportunities, Aging, Fixed Assets, Inventories, Plan) docken nach demselben Muster an. Maxime: **so schlank wie möglich** in der SQL-DB; Aufwand wandert wenn nötig in die ETL-Pipeline.

---

## 2. Designprinzipien

1. **Normalisiert speichern, denormalisiert lesen.** `fact_gl_entry` (Kopf) und `fact_gl_line` (Zeile) bleiben getrennt; Zusammenführung nur über Views.
2. **Entity ist in die Schlüssel eingebettet** (statt eigener `legal_entity_code`-Spalte). Die **ersten zwei Ziffern** eines zusammengesetzten Schlüssels = Entity-Prefix. Eine `dim_legal_entity` ordnet Prefix → Entity-Bezeichnung.
3. **Ein Betragsfeld, vorzeichenbehaftet.** `amount` signiert (`+`=Soll, `−`=Haben). Kein `debit_credit_flag`.
4. **Join auf `account_number_group`** (= Entity-Prefix ⊕ Kontonummer) — in Echtdaten **1:1** und damit fan-out-frei (§3.0).
5. **Schlanke Tabellen.** Keine Spalte ohne aktiven Consumer; abgeleitete Werte über *generated columns* oder Views, nicht als gepflegte Spalten.
6. **Modularität:** jedes Modul eigenständig; Frontend rendert datengetrieben (§8).

---

## 3. Kritische Würdigung & Verifikation

### 3.0 Verifikation des Schlüsseldesigns (Echtdaten)
Geprüft an `analytics-layer/csv_layer_canonical/dim_gl_account.csv` (1.102 Konten):

| Befund | Ergebnis |
|--------|----------|
| `account_group_code`-Format | `[Entity-Ziffer][Null-Padding][gl_account_id]`, 7-stellig (z. B. `gl_account_id 38235` in Entity 3 → `3038235`) |
| Entity-Ziffer ablesbar? | **Ja, erste Ziffer.** Verteilung 1:390, 2:378, 3:168, 4:59, 5:106 |
| Endet jede Gruppe auf ihre `gl_account_id`? | **Ja, 0 Ausnahmen** |
| Kardinalität `account_group_code` ↔ `gl_account_id` | **1:1** (1.102 ↔ 1.102) → **kein Fan-out** beim Join |
| `statement_type` vs `level_0` (Entscheidung 3) | **wertgleich**: beide `{PL:782, BS:320}`, Kreuztabelle perfekt diagonal (BS→BS, PL→PL), 0 NA, 0 Mehrdeutigkeit → `statement_type` redundant, durch `level_0` ersetzbar |

→ Deine Konvention ist bestätigt. Der frühere „1:n"-Einwand stammte aus **synthetischen Demo-Daten** (`demo_data/.../GRP1`, 40 Konten/Gruppe) und war falsch.
→ Hinweis: In den Echtdaten ist der Entity-Teil aktuell **1-stellig** (7-stellige Gruppe = 1 Ziffer + 6 Konto). Ab v0.3 standardisieren **wir** auf **2-stellig** (die Pipeline baut den Schlüssel ohnehin selbst, §4.0).

### 3.1 Übernommene Entscheidungen
| Deine Anregung | Umsetzung |
|----------------|-----------|
| `legal_entity_code` als Spalte entfernen, Entity in Schlüssel einbetten | ✅ via `journal_entry_group_number` + `account_number_group` |
| `journal_entry_group_number` = Entity-Ziffer ⊕ `journal_entry_number` | ✅ neuer Entry-/Line-Schlüssel |
| `account_number_group` = Entity-Ziffer ⊕ `gl_account_id`, als Join-Key | ✅ Join `fact_gl_line` ↔ `dim_gl_account` |
| `dim` mit Prefix → Entity-Bezeichnung | ✅ `dim_legal_entity.entity_prefix` |
| `posting_transaction_id`, `account_role`, `source_no`, alle `is_*`, `source_sheet`, `balance_sheet_flag`, `l8*`, `level_5_reporting`, `label_*`, `qa_note`, `fs_line_item`, `category_*`, `erp_module_hint`, `cashflow_category` | ✅ alle raus (Usage + Change-Liste in [Migrationsplan §3](./gl-migration-plan.md)) |
| `l9` → `l4_sub`; `l10_ic` → `is_ic`; `dim_gl_na`, `dim_gl_cf` auslagern | ✅ |
| Tabellen-Rename `fact_gl_entry`/`fact_gl_line` | ✅ |
| Konsolidierung (13. Periode) **und** IC-Flag | ✅ §7 |

### 3.2 Mein verbleibender kritischer Vorbehalt (umgesetzt, aber zu kennen)
| Thema | Risiko | Lösung im Modell |
|-------|--------|------------------|
| **Entity als Prefix = „Smart Key"** | (a) deckelt bei **≤ 99 Entities** (2-stellig, v0.3); (b) Entity-Filter werden zu String-Ops (`LEFT(key,2)`) statt sauberem FK; (c) keine referenzielle Integrität zur Entity | Da **wir** den Schlüssel bauen: festes 2-stelliges Format; `dim_legal_entity.entity_prefix` als Zuordnung; **generated column** `entity_prefix` auf den Facts (abgeleitet, kein Pflegeaufwand) für schnelle, indizierbare Filter. |
| `gl_account_id` nicht mehr auf der Line | Cross-Entity-Kontofilter brauchen die nackte Kontonummer | `gl_account_id` lebt in `dim_gl_account`; für Konto-übergreifende Filter über die Dim joinen. Optional generated column auf der Line. |

### 3.3 Weiterhin behalten (aktiv genutzt — nicht entfernen)
`posting_type` (GL-Drilldown), `level_2_sort`/`level_3_sort` (Sortierung), `level_0` (übernimmt den BS/PL-Filter von `statement_type`), `vat_amount` (USt-Abstimmung, vorgehalten), `l6_na_mapping` (Working Capital — verschoben nach `dim_gl_na`, nicht gelöscht), `cf_l11_*`/`cf_mapping` (Cashflow — nach `dim_gl_cf`).

---

## 4. Zieltabellen (minimal)

### 4.0 Schlüsselformate (verbindlich, v0.3)
- **`entity_prefix`** — **2-stellig** (`01`–`99`), durch uns vergeben. Zuordnung in `dim_legal_entity`.
- **`account_number_group`** = `entity_prefix` ⊕ `gl_account_id` (zero-pad auf 6) = **8-stellig**. Join-Key Line↔Konto.
- **`journal_entry_group_number`** = `entity_prefix` ⊕ `journal_entry_number` (10-stellig zero-padded) = **12 Zeichen**. Join-Key Entry↔Line.
- Entity überall = `LEFT(<key>, 2)` (bzw. generated column `entity_prefix`).

### 4.1 `fact_gl_entry` (Buchungskopf)
Eine Zeile pro Buchung.

| Feld | Typ | Null? | Bemerkung |
|------|-----|-------|-----------|
| `journal_entry_group_number` | VARCHAR(12) | NN | **PK**. Entity-Prefix ⊕ Buchungsnummer. |
| `fiscal_year` | SMALLINT | NN | **PK**. |
| `fiscal_period` | SMALLINT | NN | 1–12 Monat, **13 = Konsolidierung/Jahresabschluss** (§7). |
| `entry_type` | VARCHAR(16) | NN | `actual`/`consolidation`/`adjustment`, Default `actual`. |
| `posting_date` | DATE | NN | |
| `document_date` | DATE | Y | |
| `document_type_code` | VARCHAR(20) | Y | Text via Lookup im View. |
| `reference_document_number` | VARCHAR(80) | Y | GoBD „Document number". |
| `currency_code` | CHAR(5) | NN | Default `EUR`. |
| `header_note` | VARCHAR(500) | Y | optional (Audit). |
| `source_system` | VARCHAR(40) | NN | |
| `entity_prefix` | CHAR(2) | — | **GENERATED** `LEFT(journal_entry_group_number,2)` (optional, für Filter/Index). |

**Entfällt:** `legal_entity_code`, `journal_entry_number` (in PK eingebettet, via `SUBSTRING` ableitbar), `document_type_text`, `posting_transaction_id`.
**PK:** `(journal_entry_group_number, fiscal_year)`.

### 4.2 `fact_gl_line` (Buchungszeile)
| Feld | Typ | Null? | Bemerkung |
|------|-----|-------|-----------|
| `journal_entry_group_number` | VARCHAR(12) | NN | **PK** + FK → `fact_gl_entry`. |
| `fiscal_year` | SMALLINT | NN | **PK** + FK. |
| `line_number` | INTEGER | NN | **PK**. |
| `booking_line_id` | BIGINT | NN, UNIQUE | global stabil (Cursor-Pagination). |
| `account_number_group` | VARCHAR(8) | NN | **FK → `dim_gl_account`. Join-Key.** |
| `amount` | NUMERIC(18,6) | NN | **signiert** (`+`=Soll, `−`=Haben). |
| `vat_amount` | NUMERIC(18,6) | Y | vorgehalten (derzeit nicht konsumiert). |
| `line_note` | VARCHAR(500) | Y | GoBD „Booking text". |
| `customer_id` | VARCHAR(32) | Y | FK → `dim_customer`. |
| `supplier_id` | VARCHAR(32) | Y | FK → `dim_supplier`. |
| `posting_type` | VARCHAR(80) | Y | im Drilldown genutzt. |
| `source_system` | VARCHAR(40) | NN | |
| `entity_prefix` | CHAR(2) | — | **GENERATED** `LEFT(account_number_group,2)` (optional). |

**Entfällt:** `legal_entity_code`, `gl_account_id` (lebt in Dim, via `account_number_group` ableitbar), `account_role`, `source_no`, `source_type`, `debit_credit_flag`, `source_booking_number`.
**PK:** `(journal_entry_group_number, fiscal_year, line_number)` · **FK:** → `fact_gl_entry`, → `dim_gl_account`.

### 4.3 `dim_gl_account` (Kontenstamm / Mapping)
Eine Zeile pro (Entity, Konto[, Jahr]). **PK = `(account_number_group, fiscal_year)`** (siehe §8.4 — unterstützt Mapping je Entity *und* Jahr nativ).

| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `account_number_group` | VARCHAR(8) **PK** | Entity-Prefix ⊕ Kontonummer. Join-Key. |
| `fiscal_year` | SMALLINT **PK** | Mapping-Jahr (§8.4). |
| `gl_account_id` | VARCHAR(32) | nackte Kontonummer (Attribut). |
| `account_name` | VARCHAR(500) | |
| `level_0` | VARCHAR(200) | **trägt BS/PL** (ersetzt `statement_type`, §3.0). |
| `level_1` … `level_4` | VARCHAR | Hierarchie. |
| `l4_sub` | VARCHAR(200) | = bisher `l9`. |
| `level_2_sort`, `level_3_sort` | INTEGER | Sortierung (aktiv, behalten). |
| `is_ic` | BOOLEAN | aus `l10_ic` (`IC`→true). §7 Fall 2. |
| `source_system` | VARCHAR(40) | |
| `entity_prefix` | CHAR(2) | **GENERATED** `LEFT(account_number_group,2)` (optional). |

**Entfällt:** `statement_type` (→ `level_0`), alle `is_*` (außer neuem `is_ic`), `source_sheet`, `balance_sheet_flag`, `level_5_reporting`, `l8_qoe`, `l8_reported_nwc`, `qa_note`, `fs_line_item`, `category_level_*`, `erp_module_hint`, `cashflow_category`, `label_de`, `label_en`. **Verschoben:** `l6_na_mapping`/`l7_na_description` → `dim_gl_na`; alle `cf_*` → `dim_gl_cf`.

### 4.4 `dim_gl_na` (Net-Assets / NWC) — NEU
**Key = `(account_number_group, fiscal_year)`**, FK → `dim_gl_account`.

| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `account_number_group` | VARCHAR(8) **PK** | Join-Key. |
| `fiscal_year` | SMALLINT **PK** | **nötig** — NA-Mapping kann je Jahr abweichen (v0.4). |
| `l6_na_mapping` | VARCHAR(120) | **kritisch** für WC (TWC/OWC). |
| `l7_na_description` | VARCHAR(500) | |

### 4.5 `dim_gl_cf` (Cashflow) — NEU
**Key = `(account_number_group, fiscal_year)`**, FK → `dim_gl_account`.

| Feld | Typ | bisher |
|------|-----|--------|
| `account_number_group` | VARCHAR(8) **PK** | Join-Key |
| `fiscal_year` | SMALLINT **PK** | **nötig** — CF-Mapping kann je Jahr abweichen (v0.4). |
| `l1`…`l5` | VARCHAR | `cf_l11_1`, `cf_l11_2`, `cf_l11_3`, `cf_l12`, `cf_l13` |
| `cf_mapping` | VARCHAR(200) | **gespeichert** (kombiniertes CF-Mapping, v0.4) — nicht im View abgeleitet. |

### 4.6 `dim_legal_entity` (Entity-Stamm) — angepasst
| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `entity_prefix` | CHAR(2) **UNIQUE** | **NEU** — das in den Schlüsseln verwendete 2-stellige Prefix. |
| `legal_entity_code` | VARCHAR(20) **PK** | bestehender Code. |
| `entity_name` | VARCHAR(200) | |
| `is_consolidation` | BOOLEAN | **NEU** — markiert die Konzern-/Konsolidierungs-Entity (§7 Fall 1). |
| … | | bestehende Felder (currency, country, …). |

### 4.7 Referenziert (unverändert)
`dim_customer`, `dim_supplier`, `dim_document_type` (Lookup Code→Text).

---

## 5. Joins & Views

```
dim_legal_entity 1 ──(entity_prefix = LEFT(key,2))── fact_gl_entry 1 ──< fact_gl_line >── 1 dim_gl_account
                                                                              │ (account_number_group, fiscal_year)
                                                                              ├──< dim_gl_na   (1:1)
                                                                              └──< dim_gl_cf   (1:1)
```

- **Kopf ↔ Zeile:** `(journal_entry_group_number, fiscal_year)`.
- **Zeile ↔ Konto/NA/CF:** `(account_number_group, fiscal_year)` — **1:1, fan-out-frei** (§3.0).
- **Entity:** `LEFT(<key>,2) = dim_legal_entity.entity_prefix` (oder generated column).

### 5.1 `v_gl_line_enriched` (Ziel-View)
```sql
CREATE OR REPLACE VIEW v_gl_line_enriched AS
SELECT
    l.booking_line_id,
    l.journal_entry_group_number,
    LEFT(l.account_number_group, 2)        AS entity_prefix,
    le.legal_entity_code, le.entity_name,
    l.fiscal_year, l.line_number,
    e.fiscal_period, e.entry_type,
    e.posting_date, e.document_date,
    e.document_type_code, dt.document_type_text,
    e.reference_document_number,
    l.account_number_group, a.gl_account_id, a.account_name,
    a.level_0, a.level_1, a.level_2, a.level_3, a.level_4, a.l4_sub,
    a.level_2_sort, a.level_3_sort, a.is_ic,
    na.l6_na_mapping, na.l7_na_description,
    cf.l1 AS cf_l1, cf.l2 AS cf_l2, cf.l3 AS cf_l3, cf.l4 AS cf_l4, cf.l5 AS cf_l5, cf.cf_mapping,
    l.amount, l.vat_amount, l.posting_type,
    l.customer_id, l.supplier_id
FROM fact_gl_line l
JOIN fact_gl_entry e
  ON  e.journal_entry_group_number = l.journal_entry_group_number
  AND e.fiscal_year                = l.fiscal_year
JOIN dim_gl_account a
  ON  a.account_number_group = l.account_number_group
  AND a.fiscal_year          = l.fiscal_year
LEFT JOIN dim_gl_na na ON na.account_number_group = l.account_number_group AND na.fiscal_year = l.fiscal_year
LEFT JOIN dim_gl_cf cf ON cf.account_number_group = l.account_number_group AND cf.fiscal_year = l.fiscal_year
LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(l.account_number_group, 2)
LEFT JOIN dim_document_type dt ON dt.document_type_code = e.document_type_code;
```

- `JOIN dim_gl_account` INNER → Unmapped-Account-Validierung bleibt Pflicht-Gate.
- Granularität = eine Zeile pro Buchungszeile. „Pro Buchung"-Kennzahlen über `fact_gl_entry`.

### 5.2 Weitere Views (optional)
`v_gl_entry_balance` (Soll=Haben je Buchung), `v_gl_reported`/`v_gl_consolidated` (Filter `entry_type`/`fiscal_period`/`is_ic`).

---

## 6. Sign-Konvention (verbindlich)
`amount` signiert: `+`=Soll, `−`=Haben. Buchung balanciert, wenn `SUM(amount)` je Buchung ≈ 0 (Toleranz 0,01). Ersetzt das ETL-Doppelmodell (abs + `debit_credit_flag`).

> ⚠️ **Hard Rule:** Sign-Konvention nie still ändern. Jede betroffene KPI braucht Formel + Beispiel + Edge Cases + Regressionstest. Umstellung gesondert im Migrationsplan (M3).

---

## 7. IC & Konsolidierung
**Fall 1 — Konsolidierungsbogen als 13. Periode:** Werte als echte Buchungen unter einer **Konsolidierungs-Entity** (`dim_legal_entity.is_consolidation = true`, eigene `entity_prefix`) mit `fiscal_period = 13`, `entry_type = 'consolidation'`. Alle Consumer lesen weiter `fact_gl_line`; Filter über `entry_type`/`fiscal_period`. **Empfehlung: kein separater Tabellen-Zoo** (separate Tabelle würde überall `UNION` erzwingen).

**Fall 2 — IC-Konten:** `dim_gl_account.is_ic` (aus `l10_ic`); jede Buchung auf ein IC-Konto = Intercompany, über den View filterbar.

> Beide Fälle nutzen das eingebettete Entity-Schema: die Konzern-Entity ist einfach eine weitere `entity_prefix`.

---

## 8. Modularität & Ingestion

### 8.1 Modul-Prinzip
| Modul | Kerntabellen | Mindest-Input |
|-------|--------------|---------------|
| **GL** | `fact_gl_entry`, `fact_gl_line`, `dim_gl_account` (+`dim_gl_na`,`dim_gl_cf`) | GL entries+lines + Account Mapping |
| Sales | `fact_sales`, `dim_customer`, `dim_product` | Sales-Datei |
| Aging | `fact_ar_ledger`, `fact_ap_ledger` | offene Posten |
| Fixed Assets / Inventories / Opportunities / Plan | je eigenes fact/dim-Set | je Quelldatei |

Minimaler Case = nur GL **oder** nur Sales. Module teilen gemeinsame Dims, keines ist Voraussetzung für ein anderes.

### 8.2 Frontend-Verfügbarkeit (Capability-Modell)
- `/api/v1/capabilities` liefert je Projekt/Entity, welche Module Daten haben.
- Frontend rendert datengetrieben (vorhandene Module aktiv, fehlende ausgeblendet).
- Quelle: `meta_dataset_load` (welcher Datensatz wann je Entity/Jahr geladen).

### 8.3 Upload-Pipeline (Drag & Drop + Spalten-Mapping)
1. **Typisierte Dropzones:** „GL Entries & Lines" (csv/parquet/xlsx), „Account Mapping je Entity/Jahr", „Mapping konsolidierte Gruppe".
2. **Parsing & Header-Erkennung** (Backend; existiert teilweise im FDD-Upload).
3. **Mapping-Dialog:** technische Felder ⟵ Quellspalten-Dropdown; Pflicht/Optional markiert; Mapping als **wiederverwendbares Profil** je `source_system`/Mandant speichern.
4. **Schlüsselbau:** Pipeline vergibt `entity_prefix` (2-stellig) und konstruiert `account_number_group` und `journal_entry_group_number` deterministisch (nicht aus der Quelle übernehmen).
5. **Validierung:** Pflichtfelder, Typen, Soll=Haben, Unmapped-Accounts, Entity/Jahr-Konsistenz, **Format-Check der Schlüssel** (2-stelliger Entity-Prefix).
6. **Staging → Canonical Load** (idempotent je Entity/Jahr; GL-Default **Scope-Replace** mit versioniertem Snapshot je `load_id`).

### 8.3.1 Snapshot-Tabellen (Data Versioning, v0.8)
Post-Commit-Kopien des betroffenen Scopes, PK enthält `load_id`:
- `snap_fact_gl_entry`, `snap_fact_gl_line`
- `snap_dim_gl_account`, `snap_dim_gl_na`, `snap_dim_gl_cf`

`meta_dataset_load` erweitert um `scope_entity_prefixes`, `scope_fiscal_years`, `commit_mode`, `snapshot_captured`, `restored_from_load_id`. Abgeleitete Facts werden **nicht** snapshotiert — bei Restore aus GL-Snapshots neu abgeleitet.

### 8.4 Mapping je Entity **und** Jahr — gelöst (Entscheidung 2)
`dim_gl_account`/`dim_gl_na`/`dim_gl_cf` haben **`fiscal_year` im PK**. Damit ist das Mapping pro (Entity, Konto, Jahr) nativ abbildbar; Joins binden `fiscal_year` mit ein (§5.1). Versionierung läuft **allein über `fiscal_year`** — kein `valid_from/valid_to`. Mild redundant, falls Hierarchie über Jahre konstant — akzeptiert zugunsten Einfachheit & Flexibilität.

### 8.5 Feld-Manifest (Auszug GL)
| Canonical Feld | Tabelle | Pflicht | Quelle (Beispiel) | Bau durch Pipeline |
|----------------|---------|---------|-------------------|--------------------|
| `entity_prefix` | alle | ✔ | von uns vergeben (2-stellig) | ✔ |
| `journal_entry_group_number` | entry/line | ✔ | `entity_prefix` ⊕ „Transaction number" | ✔ |
| `account_number_group` | line/dim | ✔ | `entity_prefix` ⊕ „Account number" | ✔ |
| `fiscal_year` | alle | ✔ | „Year" | |
| `posting_date` | entry | ✔ | „Posting date" | |
| `amount` | line | ✔ | „Amount" (→ signiert) | ✔ |
| `gl_account_id` | dim | ✔ | „Account number" | |
| `level_0..4`, `l4_sub` | dim | ✔ (Mapping) | Mapping-Hierarchie | |
| `is_ic` | dim | – | Mapping „L10 IC" | ✔ |
| `l6_na_mapping` | dim_gl_na | – | Mapping „L6 NA" | |
| `customer/supplier` | line | – | „Source type" + „Source No." | ✔ |

---

## 9. Entscheidungen v0.3 (erledigt)
- ✅ **Entity-Prefix 2-stellig** (Skalierung bis 99 Entities).
- ✅ **Mapping-Versionierung allein über `fiscal_year`** (kein `valid_from/valid_to`).
- ✅ **`statement_type` entfällt → `level_0`** (in Echtdaten wertgleich, §3.0).

## 10. Entscheidungen v0.4 (erledigt)
- ✅ **NA/CF-Grain:** Mapping **nicht zwingend konstant** über Jahre → `fiscal_year` bleibt im PK von `dim_gl_na`/`dim_gl_cf`.
- ✅ **Sortierung:** bleibt über `level_2_sort`/`level_3_sort` (technisch sauberer).
- ✅ **`cf_mapping`:** als Spalte in `dim_gl_cf` **gespeichert**.

## 11. Offene Punkte
- GL-Kernstruktur: nichts mehr offen. Nächster Schritt: Migrations-Umsetzung (siehe `gl-migration-plan.md`, ab M0).
- Abgeleitete Fact-Tables AR/AP/Sales/CoM: siehe §12 (v0.5); offene Detailpunkte am Ende von §12.

---

## 12. Abgeleitete Fact-Tables: AR, AP, Sales, CoM (v0.5)

> Quelle der Logik: Beschreibung Benedikt (GoBD-Client) + Analyse des KNIME-Workflows `DATEV_SKR03.knwf` (DATEV-Methode). Ein vom User geliefertes Bildbeispiel verfeinert später die GoBD-Propagation (§12.3).

### 12.0 Einordnung
Diese vier Tabellen sind **keine** neu eingelesenen Rohdaten, sondern werden **aus dem kanonischen GL abgeleitet** (`fact_gl_line` + `fact_gl_entry` + `dim_gl_account`) — eine **Mart-/Ableitungsschicht**. Kontoklassifikation (Receivable/Payable/Revenue/Material) kommt aus `dim_gl_account` (Hierarchie `level_2/3/4`, NA-Mapping) — **kein** Hardcoding von Kontonummern in der DB (analog SKR03 „Pos / Aktiva-Passiva-GuV").

### 12.1 Grundidee
- **fact_ar** / **fact_ap**: GL-Zeilen auf Debitoren-/Kreditorenkonten (Personenkonten) → Subledger / offene Posten, mit `customer_id`/`supplier_id`.
- **fact_sales** (gross sales): GL-Zeilen auf Umsatzkonten, angereichert um den **Kunden** der zugehörigen AR-Buchung.
- **fact_com** (cost of materials): GL-Zeilen auf Material-/Wareneinsatzkonten, angereichert um den **Lieferanten** der zugehörigen AP-Buchung.

### 12.2 Der Kern: Personenkonto ↔ GuV-Konto verknüpfen — zwei Methoden
Die Debitoren-/Kreditorennummer hängt am **Personenkonto**, der Umsatz-/Material-Betrag am **GuV-Konto**. Beide müssen zusammengeführt werden. Wie, hängt vom Quellsystem ab:

**Methode A — Transaction-Number-Propagation (GoBD / aktueller Client)**
- Eine Buchung (`journal_entry_group_number`) gruppiert mehrere Zeilen.
- Die AR-Zeile trägt `customer_id` (aus „Source No." bei „Source type = Debitor"), die Umsatzzeile nicht.
- **Propagiere** `customer_id` von der AR-Zeile auf die Umsatzzeile(n) derselben Buchung; analog `supplier_id` (AP) → Materialzeile.
- Voraussetzung: (1) Partnernummer steht auf der AR/AP-Zeile, (2) die Buchung verknüpft Personenkonto und GuV-Konto über die Transaction number.

**Methode B — Konto/Gegenkonto (DATEV SKR03)**
- Jede DATEV-Zeile enthält `Konto` **und** `Gegenkonto` (+ „Rolle des Kontos im Buchungssatz": 1=Kontofeld, 2=Gegenkontofeld). Personenkonto und GuV-Konto stehen **in einer Zeile** gegenüber.
- Beide Konten über das Mapping anreichern (KNIME: Joiner auf `Konto` *und* auf `Gegenkonto` → `Pos 1/2`, `Aktiva/Passiva/GuV`, `Beschriftung`); das Personenkonto liefert den Partner, das GuV-Konto die Umsatz-/Material-Klassifikation.
- Betrag signiert = `Umsatz Soll − Umsatz Haben` (KNIME: Math Formula #80).
- **Kein** Cross-Row-Propagieren nötig.

### 12.3 Quellabhängige Erkennung (Pipeline-Setup — vorgemerkt)
Die Vorgehensweise trifft **nicht immer** zu. Das Projekt-Setup braucht einen **Konfigurations-/Erkennungsschritt**, den unsere Mitarbeiter im Frontend bedienen:
1. In welcher Spalte steht die Debitoren-/Kreditorennummer? (z. B. „Source No.", `Konto`, `Gegenkonto`, separates Feld)
2. Steht sie bei den Sales-/Expense-Buchungen mit dabei bzw. ist sie verknüpfbar (über Transaction number / Gegenkonto)?
3. Daraus **Linking-Strategie** wählen: **A** (Propagation), **B** (Konto/Gegenkonto) oder **Fallback** (Sales/CoM ohne Partnerzuordnung, nur Betrag).
→ Im Feld-Manifest (§8.5) als konfigurierbare „Linking-Strategie" je Quelle/Mandant führen.

### 12.4 Zieltabellen (Entwurf)
Alle referenzieren `booking_line_id` (Rückverfolgbarkeit) und tragen `link_method` (`txn` | `gegenkonto` | `none`).

**fact_ar / fact_ap** (Subledger / offene Posten)
| Feld | Bemerkung |
|------|-----------|
| `booking_line_id` **PK** | = GL-Zeile |
| `journal_entry_group_number`, `fiscal_year`, `line_number` | GL-Verknüpfung |
| `account_number_group` | Debitoren-/Kreditorenkonto |
| `customer_id` / `supplier_id` | Partner (FK) |
| `posting_date`, `document_date`, `due_date` | Fälligkeit (sofern vorhanden) → Aging |
| `amount` (signiert) | offener Betrag |
| `reference_document_number` | Beleg |
| `entry_type`, `source_system` | |

> **Reconcile:** Es existieren bereits `fact_ar_ledger`/`fact_ap_ledger` + `ar_aging.py`/`ap_aging.py`. In der Migration klären, ob `fact_ar`/`fact_ap` diese **ersetzen/vereinheitlichen** oder als GL-abgeleitete Sicht daneben stehen. Nicht doppelt pflegen.

**fact_sales** (gross sales)
| Feld | Bemerkung |
|------|-----------|
| `booking_line_id` **PK** | = Umsatz-GL-Zeile |
| `journal_entry_group_number`, `fiscal_year` | GL-Verknüpfung |
| `account_number_group` | Umsatzkonto |
| `customer_id` | **attribuiert** (Methode A/B) |
| `posting_date` | |
| `gross_sales` | = `−amount` (§12.5) |
| `link_method` | Nachvollziehbarkeit |
| `entry_type`, `source_system` | |

**fact_com** (cost of materials)
| Feld | Bemerkung |
|------|-----------|
| `booking_line_id` **PK** | = Material-GL-Zeile |
| `journal_entry_group_number`, `fiscal_year` | GL-Verknüpfung |
| `account_number_group` | Material-/Wareneinsatzkonto |
| `supplier_id` | **attribuiert** (Methode A/B) |
| `posting_date` | |
| `cost_of_materials` | = `amount` (§12.5) |
| `link_method`, `entry_type`, `source_system` | |

### 12.5 Sign-Konvention & Mini-Beispiel
`amount` signiert (`+`=Soll, `−`=Haben).
- **gross_sales** = `−SUM(amount)` über Umsatzkonten (Umsatzerlöse = Haben → negativ; Drehung ergibt positiven Umsatz).
- **cost_of_materials** = `SUM(amount)` über Materialkonten (Soll → positiv).

Beispiel (GoBD, Methode A): Verkauf 1.190 € brutto an Kunde C-100:
| Zeile | Konto (Klassifik.) | amount | customer_id |
|-------|--------------------|--------|-------------|
| 1 | Debitor (AR) | +1.190 | C-100 |
| 2 | Umsatzerlöse 19 % | −1.000 | (leer) |
| 3 | USt 19 % | −190 | (leer) |

→ Propagation: `customer_id=C-100` von Zeile 1 auf Zeile 2.
→ `fact_sales`: booking_line_id(Z2), customer_id=C-100, `gross_sales = −(−1.000) = 1.000`.
→ `fact_ar`: booking_line_id(Z1), customer_id=C-100, amount=+1.190.

**Edge Cases (bei Implementierung als Tests):** (a) Sammelbuchung mit mehreren Debitoren in einer Transaction → Propagation mehrdeutig → `link_method='none'` + Flag; (b) Umsatz ohne Personenkonto (Barverkauf) → kein `customer_id`; (c) Storno/Gutschrift → Vorzeichen konsistent halten; (d) USt-Zeilen **nicht** als Sales zählen (Klassifikation über Mapping); (e) DATEV: `Rolle`-Feld bestimmt, welche Seite Personen- vs GuV-Konto ist.

### 12.6 Views
- `v_sales_enriched` / `v_com_enriched`: fact + `dim_customer`/`dim_supplier` + Konto-Hierarchie.
- Aging baut auf `fact_ar`/`fact_ap` + `due_date`.

### 12.7 Offene Detailpunkte
1. **Bildbeispiel** des GoBD-Clients abwarten → exakte Spalte der Debitoren-/Kreditorennummer und Propagations-Regeln bestätigen.
2. **Reconcile** `fact_ar`/`fact_ap` vs. bestehende `fact_ar_ledger`/`fact_ap_ledger`.
3. **SKR03-Mapping** als eigene Mapping-Variante in `dim_gl_account` (Pos/Aktiva-Passiva-GuV ↔ unsere `level_*`) — Spaltenabbildung im Ingestion-Manifest.
4. **Materialisiert vs. View:** AR/AP/Sales/CoM als physische Fact-Tables (Performance, Aging) oder als Views auf `fact_gl_line`? Empfehlung: materialisiert (mit `link_method`), da Partner-Propagation teuer ist.

---

## 13. Partner- & Geo-Dimensionen (v0.6)

### 13.1 `dim_customer` / `dim_supplier` — Stammdaten 1:1, entity-aware verknüpft
Die **Attribute** werden 1:1 aus den Kunden-/Lieferantenstammdaten übernommen. Die **Verknüpfung** zu den Facts läuft — konsistent zum GL-Design — über **Entity-Prefix ⊕ Debitoren-/Kreditorennummer** (nicht über eine separate `legal_entity_code`-Spalte). Damit ist derselbe Debitor in Entity 01 vs. 02 sauber als zwei verschiedene Kunden abbildbar.

**`dim_customer`**
| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `customer_id` | VARCHAR(32) **PK** | = `entity_prefix(2)` ⊕ `debtor_number`. **Identisch mit `fact_gl_line.customer_id`** (Join-Key). |
| `entity_prefix` | CHAR(2) | **GENERATED** `LEFT(customer_id,2)`; verknüpfbar zu `dim_legal_entity.entity_prefix`. |
| `debtor_number` | VARCHAR(30) | rohe Debitorennummer (1:1 aus Stammdaten). |
| `name_line_1`, `name_line_2` | VARCHAR(200) | 1:1. |
| `country_code` | CHAR(3) | **FK → `dim_country`**. |
| `region_code` | VARCHAR(20) | **FK → `dim_region`** (mit `country_code`). |
| `city`, `postal_code` | | 1:1. |
| `default_currency` | CHAR(5) | |
| `source_system` | VARCHAR(40) | |
| `updated_at` | TIMESTAMPTZ | |

**`dim_supplier`** — identisch, statt `debtor_number` → `creditor_number`, plus `purchasing_org` (1:1).

> **Ggü. heute:** Spalte `legal_entity_code` entfällt (Entity steckt im Schlüssel, via `entity_prefix` → `dim_legal_entity` auflösbar). Alle übrigen Spalten unverändert übernommen. **Reconcile** mit der heutigen ETL-Praxis (Stubs `CUST-…`/`SUPP-…` mit leerem `legal_entity_code`) im Migrationsplan.

### 13.2 Geographie: `dim_country` + `dim_region` (empfohlener Split)
Statt einer Flachtabelle ein kleiner **Snowflake** — Begründung in §13.4.

**`dim_country`**
| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `country_code` | CHAR(3) **PK** | ISO-3166-1 alpha-3 (`DEU`). |
| `iso2` | CHAR(2) | `DE` (für Flags/Frontend). |
| `name_de` | VARCHAR(120) | „Deutschland". |
| `name_en` | VARCHAR(120) | „Germany". |
| `continent` | VARCHAR(40) | „Europa" — höchste Roll-up-Ebene. |
| `economic_area` | VARCHAR(40) | `EU` / `EFTA` / `Non-EU` … (für Wirtschaftsraum-Aggregation). |

**`dim_region`**
| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `country_code` | CHAR(3) **PK**, FK → `dim_country` | Region-Codes sind nur **innerhalb** eines Landes eindeutig. |
| `region_code` | VARCHAR(20) **PK** | roher Regions-Code (1:1 aus Stammdaten). |
| `name_de`, `name_en` | VARCHAR(120) | ausgeschriebene Bezeichnung (z. B. „Bayern"/„Bavaria"). |
| `region_type` | VARCHAR(40) | Bundesland / Sales-Region / NUTS-2 … (kennzeichnet die Art der Region). |

> **i18n-Hinweis:** Geografie ist — anders als die GL-Konto-Labels (dort `label_*` zunächst verworfen) — ein **sauberer Übersetzungsfall** (standardisierte ISO-Namen). Daher hier bewusst `name_de` + `name_en`.

### 13.3 Aggregationshierarchie (mehrere Ebenen)
```
Kunde/Lieferant ─ region_code ─►  dim_region ─ country_code ─►  dim_country ─►  continent / economic_area
   (city, postal_code)              (Region)                       (Land)            (Kontinent / Wirtschaftsraum)
```
Damit lassen sich Sales/AR/AP/CoM auf **City → Region → Land → Kontinent/Wirtschaftsraum** aggregieren. Join-Pfad einer Analyse: `fact_sales → dim_customer → dim_region → dim_country`. Jede Ebene liefert ausgeschriebene Namen fürs Frontend.

### 13.4 Warum Split statt einer `dim_region`-Flachtabelle (kritische Abwägung)
- **Keine Redundanz:** Ländername/Kontinent/Wirtschaftsraum werden **einmal** in `dim_country` gehalten statt bei jeder Region wiederholt.
- **Saubere Roll-up-Ebenen:** Region → Land → Kontinent/Wirtschaftsraum sind klar getrennt; Aggregation auf jeder Ebene ist ein einfacher GROUP BY.
- **Wiederverwendbar:** `dim_country`/`dim_region` sind geteilte Dims (Kunden, Lieferanten, perspektivisch `dim_legal_entity`).
- **Trade-off:** ein Join mehr als bei einer Flachtabelle — vernachlässigbar; das Frontend nutzt ohnehin eine View (§13.5).
- *Alternative (eine Flachtabelle `dim_region` mit Country-Spalten) nur, falls bewusst denormalisiert gewünscht — nicht empfohlen.*

### 13.5 Views
- `v_customer_geo` / `v_supplier_geo`: Partner + `dim_region` + `dim_country` (alle ausgeschriebenen Namen) — eine Quelle fürs Frontend.
- Sales/AR-Analysen joinen darüber, statt drei Dims einzeln.

### 13.6 Offene Detailpunkte
1. **Reconcile** der heutigen Partner-Stubs (`CUST-…`/`SUPP-…`, leeres `legal_entity_code`) auf das entity-aware Schema.
2. **Stammdaten-Quelle:** kommen echte Kunden-/Lieferantenstammdaten (mit Adresse/Region) als eigener Upload, oder bleiben es vorerst Stubs aus dem GL? → Ingestion-Manifest erweitern.
3. **Geo-Referenzdaten:** `dim_country` als gepflegte Referenztabelle (ISO-Liste, einmalig befüllt) vs. nur beobachtete Codes.

---

## 14. Plan-/Forecast-Tabellen (v0.7)

Plan-Werte für **alle** P&L-, BS- und CF-Positionen sowie eine **Sales-Planung nach Kunden**. Mangels echter Plandaten zunächst **synthetisch** erzeugt, im Frontend (Table View) editierbar.

### 14.1 Szenario- & Perioden-Modell
- `scenario` ∈ {`actual`, `forecast`, `plan`}.
- Horizont: **FY25F** = Forecast für die **fehlenden Monate** des laufenden Jahres; **FY26P / FY27P / FY28P / FY29P** = Voll-Jahres-Plan.
- Granularität wie Ist: `fiscal_year` + `fiscal_period` (1–12; 13 = Konsolidierung). `…F` nur offene Perioden, `…P` alle 12.

### 14.2 `fact_gl_plan` — deckt P&L, BS und CF ab
Schlank: Plan auf **derselben Kontogranularität** wie die Ist-Daten → P&L/BS/CF-Plan nutzen **dieselben** Mappings (`dim_gl_account`, `dim_gl_cf`) und dieselbe Aggregationslogik wie die Ist-Statements. **Kein** separater Plan-Tabellen-Zoo je Statement.

| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `account_number_group` | VARCHAR(8) **PK**, FK → `dim_gl_account` | wie Ist; P&L/BS/CF ergeben sich aus dem Mapping |
| `fiscal_year` | SMALLINT **PK** | |
| `fiscal_period` | SMALLINT **PK** | 1–12 (13) |
| `scenario` | VARCHAR(12) **PK** | `forecast` / `plan` |
| `amount` | NUMERIC(18,6) | **Bewegung** (signiert) |
| `is_synthetic` | BOOLEAN | true bei generierten Werten |
| `source_system` | VARCHAR(40) | |

> **BS-Plan als Bewegung** speichern (nicht Bestand), damit die kumulierte Summe analog zu den Ist-Buchungen den Bestand ergibt — gleiche Aggregation wie bei Actuals.

### 14.3 `fact_sales_plan` — Sales-Plan nach Kunden
| Feld | Typ | Bemerkung |
|------|-----|-----------|
| `customer_id` | VARCHAR(32) **PK**, FK → `dim_customer` | entity-aware |
| `fiscal_year` | SMALLINT **PK** | |
| `fiscal_period` | SMALLINT **PK** | |
| `scenario` | VARCHAR(12) **PK** | |
| `gross_sales_plan` | NUMERIC(18,6) | |
| `is_synthetic` | BOOLEAN | |

### 14.4 Nutzung
- Statement-/Sales-Endpunkte erhalten einen `scenario`-Parameter; im Year-View erscheinen Plan-Spalten **FY25F … FY29P** neben den Ist-Spalten.
- **Table View editierbar:** Plan-Zellen überschreibbar → Persistenz zurück in `fact_gl_plan`/`fact_sales_plan` (`is_synthetic=false` nach manueller Eingabe).
- Synthetische Erstgenerierung als ETL-Schritt (siehe Migrationsplan §11).
