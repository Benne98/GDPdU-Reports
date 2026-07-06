/**
 * Per-customer module allowlist (Baukasten lever).
 * Remove a key from this set to hide that module for this customer.
 * Default = all 6 modules enabled.
 *
 * HOW TO USE:
 *   To disable "FDD-Bot" for a customer, change the array to:
 *     ['reporting', 'budget', 'project-setup', 'ingestion', 'role-management']
 *   The module will disappear from the sidebar and the home redirect will skip it.
 *   No backend change is needed — this is a pure frontend config lever.
 */
export const ENABLED_MODULES = new Set<string>([
  'reporting',
  'fdd-bot',
  'budget',
  'project-setup',
  'ingestion',
  'role-management',
])
