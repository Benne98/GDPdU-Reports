# Frontend redesign + scope reduction (merged stack, port 5180)

## Goal
Modernise the merged-stack UI, reduce scope to the modules that matter, and make modules a per-customer "Baukasten". Keep the palette light + blue (`#1E3A5F` navy on `#F4F6F9`). Design references (clean, light, spacious SaaS): opsiocloud.com, next-levels.de, divint.de, anthropic.com, microsoft.com/365.

## Confirmed decisions
- **Modules kept:** Reporting, FDD-Bot, Budget Planning (+ always-on settings: Project Setup, Data Update, Role Management). **Dropped from the launcher:** Anomaly Detection (and any other tile).
- **Modulbaukasten = BOTH:** a central module **registry** + a per-customer **config allowlist** (edit one list to hide a module), AND wired to the existing **role `page_keys`** model so roles further restrict.
- **Launcher = Sidebar App-Shell:** persistent left module rail; content opens on the right. Replaces the home-tile grid. No "FOR FINSSENTIALS STAFF" divider.
- **FDD-Bot:** clicking the module opens the CHAT directly (only "Upload new data" remains → drop the intermediate choice screen); adopt the Budget-Planning layout; keep an **expandable version history**.
- **Reporting:** move the filter UP into the top bar (right of the last nav page, before the divider/account); hover a nav page shows its subpage(s); remove the subheader hint "Click a value to drill into GL lines. Use ↑ / ↓ to jump between charts."

## Grounding (verified file map)
- **Launcher/home:** `frontend/src/pages/HomePage.tsx` — hard-coded `MODULES` array + local `ModuleCard`; "For Finssentials staff" divider at line 234 (only Project Setup, `staff:true`). Routing: `frontend/src/App.tsx` (`AppRoutes`), shell = `Shell` + `frontend/src/components/AppHeader.tsx`. Auth: `frontend/src/context/AuthContext.tsx` (`is_admin`). Role model (unwired to tiles): `frontend/src/pages/RoleManagementPage.tsx` + `lib/api.ts` (`AdminRole.page_keys/entity_codes`, `api.adminPages()/adminRoles()`), `FALLBACK_PAGES` keys.
- **FDD-bot:** `frontend/src/pages/FddBotPage.tsx` — two-phase `mode` state (`null` = choice screen lines 400-433 with `ChoiceCard` 51-138 + hero 377-398; `'upload'|'pipeline'` = chat canvas 447-570). Live sidebar = inline `ChatHistorySidebar` (209-318, the version history to keep, always-on today). Chat body `components/fdd-bot/BotConversation.tsx`; store `components/fdd-bot/fddProjectStore.ts`; hook `useFddBot`. Dead: `FddChatPanel.tsx`, `FddProjectSidebar.tsx`.
- **Budget layout (mirror target):** `frontend/src/pages/BudgetChatPage.tsx` — header (eyebrow+h1) → `components/budget/chat/ChatSummaryBar.tsx` (config chips + Edit/Reset) → tab bar → `components/budget/StructuredBudgetView.tsx`, on `#F4F6F9` max-w-1680.
- **Reporting chrome:** `AppHeader.tsx` — right group order: `<nav> REPORTING_NAV pills (117-145)` → divider (183-187) → account `display_name` (190-192) → Log out (194-207). Filter is NOT here. `REPORTING_NAV` (29-36) is flat (no children). Filter component: `frontend/src/components/ui/ModulePeriodFilterBar.tsx` (+ `CollapsibleModuleFiltersCard.tsx`); on statements it renders in-body at `StatementsPage.tsx:661` behind a Filters toggle; filter state lives per-page (`StatementsPage.tsx:248-252`, OverviewPageV2 similar). Subpages = per-page `subTabs` (IncomeStatement/BalanceSheet/CashFlow pages) + nested `SalesAgingSubNav.tsx`. Drill hint: `IncomeStatementPage.tsx:14` (+ partials in WC/CF/BS pages) rendered at `StatementsPage.tsx:585-587`.
- **Palette (inline hex, no token file):** navy `#1E3A5F` (+ tints `rgba(30,58,95,0.05–0.12)`), bg `#F4F6F9`, surfaces `#FFFFFF`/`#F8FAFC`, borders `#E2E8F0`/`#CBD5E1`, text `#111827`/`#475569`/`#64748B`/`#94A3B8`.

## Implementation phases
### Phase A — Module registry + Baukasten (data layer)
- New `frontend/src/lib/moduleRegistry.ts`: single source of truth — `Module { key, label, description, icon, path, group: 'main'|'settings', adminOnly }`. Seed with the 6 kept modules (Reporting, FDD-Bot, Budget → main; Project Setup, Data Update, Role Management → settings). Anomaly NOT included.
- New per-customer config `frontend/src/config/enabledModules.ts` (a `Set<string>` of enabled module keys; edit to hide) — default = all 6. Document how to remove a module for a customer.
- Wire role `page_keys`: a module is visible iff `enabledConfig.has(key)` AND (`!adminOnly || isAdmin`) AND (user has no page-restriction OR `page_keys` includes the module's key). Verify `/auth/me` exposes the user's effective `page_keys`; if not, add it (backend-engineer: include the user's role page_keys on the auth user). Provide a `useVisibleModules()` selector.
- Replace `HomePage.tsx`'s hard-coded array with the registry (or retire HomePage — see Phase B).

### Phase B — Sidebar App-Shell (launcher redesign)
- Refactor `Shell` (App.tsx) into: persistent left **module rail** (icons+labels from `useVisibleModules()`, grouped Main / Settings, active state, collapsible on narrow) + top bar (brand, account, Log out) + content outlet. Modern light styling (whitespace, soft shadows, refined type; keep navy/blue).
- `/` → redirect to the first visible module (or a light welcome). Remove the tile grid + staff divider. Keep all existing routes; the rail drives navigation.

### Phase C — Reporting chrome
- Introduce a shared **reporting filter context** (`frontend/src/context/ReportingFilterContext.tsx`) holding `{entity, grain, period, latest}`; hoist from `StatementsPage`/`OverviewPageV2` to the provider. Render `ModulePeriodFilterBar` (compact/`embedded`) in the top bar, before the divider/account, only on reporting routes; pages consume the context.
- Add `children?: {to,label}[]` to `REPORTING_NAV` mirroring per-page `subTabs`; on hover of a nav pill show a small subpage popover.
- Remove the drill hint: clear the `description` hint text in `IncomeStatementPage.tsx` / `WorkingCapitalPage.tsx` / `CashFlowPage.tsx` / `BalanceSheetPage.tsx` (or drop the `<p>` at `StatementsPage.tsx:585-587`).

### Phase D — FDD-Bot redesign
- Default `mode` to `'upload'` and `bot.createProject('upload')` on mount; delete the hero + `choiceScreen` + `ChoiceCard` + the "Change mode" back button. Chat opens immediately.
- Restructure to the Budget layout: page header (eyebrow "FDD-Bot" + h1) → compact summary/toolbar bar (Undo/Reset) → chat content (`BotConversation`).
- Convert `ChatHistorySidebar` into an **expandable/collapsible** version-history panel (left drawer or toggle) instead of a permanent 240px column. Keep `fddProjectStore`/`useFddBot` behaviour.
- (Optional cleanup) delete dead `FddChatPanel.tsx` + `FddProjectSidebar.tsx`.

## Verification
- Quality gate: `cd frontend && npm run build` (tsc clean); merged mode `npm run dev:merged` on 5180 boots; backend import if `/auth/me` changed; relevant pytest if backend touched.
- Manual on 5180: sidebar shows only the 6 modules (Anomaly gone, no staff divider); removing a key from `enabledModules.ts` hides that module; a role with restricted `page_keys` sees fewer modules; FDD-bot opens straight into chat with a collapsible version history and Budget-like layout; Reporting filter sits in the top bar, nav hover shows subpages, drill hint gone.
- Keep it reporting-v2/merged-consistent; do not regress the other stacks (though they're paused). Palette stays light/blue.
