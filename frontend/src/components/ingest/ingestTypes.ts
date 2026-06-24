/**
 * ingestTypes.ts — shared state interfaces for the Data Update wizard step components.
 *
 * Extracted from IngestionPage.tsx so that both IngestionPage and future wizards
 * (e.g. ProjectSetupWizard) can import these types without coupling to the page.
 */

import type { EntityMode, FiscalYearMode, LinkingStrategy, SignMode } from "../../lib/gdpduApi";

// ---------------------------------------------------------------------------
// Step 1 — Kontext
// ---------------------------------------------------------------------------

export interface KontextState {
  entityMode: EntityMode;
  entityValue: string;
  fiscalYearMode: FiscalYearMode;
  fiscalYearValue: string;
}

// ---------------------------------------------------------------------------
// Step 3 — Transform options (GL only)
// ---------------------------------------------------------------------------

export interface OptionsState {
  signMode: SignMode;
  signAmount: string;
  signSoll: string;
  signHaben: string;
  signDcFlag: string;
  signDebitValue: string;
  decimal: string;
  thousands: string;
  dateDayfirst: boolean;
  linking: LinkingStrategy;
  profileName: string;
  profileSystem: string;
}
