/** Resolve country codes / abbreviations to English display names. */
const ISO3: Record<string, string> = {
  DEU: 'Germany',
  FRA: 'France',
  GBR: 'United Kingdom',
  NLD: 'Netherlands',
  AUT: 'Austria',
  CHE: 'Switzerland',
  ITA: 'Italy',
  ESP: 'Spain',
  POL: 'Poland',
  USA: 'United States',
  CAN: 'Canada',
  MEX: 'Mexico',
  BRA: 'Brazil',
  ARG: 'Argentina',
  CHN: 'China',
  JPN: 'Japan',
  IND: 'India',
  AUS: 'Australia',
  BEL: 'Belgium',
  CZE: 'Czech Republic',
  HUN: 'Hungary',
  IRL: 'Ireland',
  LUX: 'Luxembourg',
  ROU: 'Romania',
  SVK: 'Slovakia',
  UNK: 'Unknown',
}

const ISO2: Record<string, string> = {
  DE: 'Germany',
  FR: 'France',
  GB: 'United Kingdom',
  UK: 'United Kingdom',
  NL: 'Netherlands',
  AT: 'Austria',
  CH: 'Switzerland',
  IT: 'Italy',
  ES: 'Spain',
  PL: 'Poland',
  US: 'United States',
  CN: 'China',
  BE: 'Belgium',
  CZ: 'Czech Republic',
  HU: 'Hungary',
  IE: 'Ireland',
  LU: 'Luxembourg',
  RO: 'Romania',
  SK: 'Slovakia',
}

const ISO2_TO_ISO3: Record<string, string> = {
  DE: 'DEU',
  FR: 'FRA',
  GB: 'GBR',
  UK: 'GBR',
  NL: 'NLD',
  AT: 'AUT',
  CH: 'CHE',
  IT: 'ITA',
  ES: 'ESP',
  PL: 'POL',
  US: 'USA',
  CN: 'CHN',
  BE: 'BEL',
  CZ: 'CZE',
  HU: 'HUN',
  IE: 'IRL',
  LU: 'LUX',
  RO: 'ROU',
  SK: 'SVK',
}

/** Demo / legacy shorthand seen in AR customer master. */
const LEGACY: Record<string, string> = {
  D: 'Germany',
}

const LEGACY_TO_ISO3: Record<string, string> = {
  D: 'DEU',
}

export function countryDisplayName(code: string | null | undefined): string {
  const raw = String(code ?? '').trim()
  if (!raw || raw === '—') return raw || '—'
  const up = raw.toUpperCase()
  if (LEGACY[up]) return LEGACY[up]
  if (ISO3[up]) return ISO3[up]
  if (ISO2[up]) return ISO2[up]
  if (up.length === 3 && ISO3[up.slice(0, 3)]) return ISO3[up.slice(0, 3)]
  return raw
}

/** Canonical ISO-3 key for grouping country rows (D/DE/DEU → DEU). */
export function countryGroupKey(code: string | null | undefined): string {
  const raw = String(code ?? '').trim()
  if (!raw || raw === '—') return '—'
  const up = raw.toUpperCase()
  if (LEGACY_TO_ISO3[up]) return LEGACY_TO_ISO3[up]
  if (ISO3[up]) return up
  if (ISO2_TO_ISO3[up]) return ISO2_TO_ISO3[up]
  const byName = Object.entries(ISO3).find(([, name]) => name.toUpperCase() === up)?.[0]
  return byName ?? up
}
