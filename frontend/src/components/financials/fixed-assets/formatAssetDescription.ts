/** Anlage | Anlagenbezeichnung — matches backend format_asset_description. */
export function formatAssetDescription(
  assetId?: string | null,
  assetLabel?: string | null,
  fallbackLabel?: string | null,
): string {
  const id = assetId?.trim()
  const lbl = assetLabel?.trim()
  if (id && lbl) return `${id} | ${lbl}`
  if (id) return id
  if (lbl) return lbl
  return fallbackLabel?.trim() || '—'
}
