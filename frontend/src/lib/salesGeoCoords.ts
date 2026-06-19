/**
 * Client-side geo fallback (mirrors backend geo_location_lookup).
 * Ensures bubble maps work even when API returns rows without lat/lon.
 */
import type { SalesGeoLocation } from './api'

const ISO3_TO_NAME: Record<string, string> = {
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
  CHN: 'China',
  JPN: 'Japan',
  IND: 'India',
  AUS: 'Australia',
}

const ISO_TO_NAME: Record<string, string> = {
  AT: 'Austria',
  BE: 'Belgium',
  CH: 'Switzerland',
  CN: 'China',
  CZ: 'Czech Republic',
  DE: 'Germany',
  ES: 'Spain',
  FR: 'France',
  GB: 'United Kingdom',
  HU: 'Hungary',
  IE: 'Ireland',
  IT: 'Italy',
  LU: 'Luxembourg',
  NL: 'Netherlands',
  PL: 'Poland',
  RO: 'Romania',
  SK: 'Slovakia',
  UK: 'United Kingdom',
  US: 'United States',
}

const CITY_SEED: Record<string, { lat: number; lon: number }> = {
  'munich|germany': { lat: 48.137, lon: 11.576 },
  'münchen|germany': { lat: 48.137, lon: 11.576 },
  'munich|de': { lat: 48.137, lon: 11.576 },
  'münchen|de': { lat: 48.137, lon: 11.576 },
  'berlin|germany': { lat: 52.52, lon: 13.405 },
  'berlin|de': { lat: 52.52, lon: 13.405 },
  'stuttgart|germany': { lat: 48.775, lon: 9.183 },
  'stuttgart|de': { lat: 48.775, lon: 9.183 },
  'aachen|germany': { lat: 50.776, lon: 6.083 },
  'aachen|de': { lat: 50.776, lon: 6.083 },
  'unna|germany': { lat: 51.534, lon: 7.689 },
  'unna|de': { lat: 51.534, lon: 7.689 },
  'paris|france': { lat: 48.857, lon: 2.352 },
  'paris|fr': { lat: 48.857, lon: 2.352 },
}

const COUNTRY_CENTROIDS: Record<string, { lat: number; lon: number }> = {
  austria: { lat: 47.52, lon: 14.55 },
  at: { lat: 47.52, lon: 14.55 },
  belgium: { lat: 50.5, lon: 4.47 },
  be: { lat: 50.5, lon: 4.47 },
  switzerland: { lat: 46.82, lon: 8.23 },
  ch: { lat: 46.82, lon: 8.23 },
  china: { lat: 35.86, lon: 104.2 },
  cn: { lat: 35.86, lon: 104.2 },
  'czech republic': { lat: 49.82, lon: 15.47 },
  cz: { lat: 49.82, lon: 15.47 },
  germany: { lat: 51.16, lon: 10.45 },
  de: { lat: 51.16, lon: 10.45 },
  deu: { lat: 51.16, lon: 10.45 },
  spain: { lat: 40.46, lon: -3.75 },
  es: { lat: 40.46, lon: -3.75 },
  france: { lat: 46.23, lon: 2.21 },
  fr: { lat: 46.23, lon: 2.21 },
  'united kingdom': { lat: 55.38, lon: -3.44 },
  uk: { lat: 55.38, lon: -3.44 },
  gb: { lat: 55.38, lon: -3.44 },
  hungary: { lat: 47.16, lon: 19.5 },
  hu: { lat: 47.16, lon: 19.5 },
  ireland: { lat: 53.14, lon: -7.69 },
  ie: { lat: 53.14, lon: -7.69 },
  italy: { lat: 41.87, lon: 12.57 },
  it: { lat: 41.87, lon: 12.57 },
  luxembourg: { lat: 49.82, lon: 6.13 },
  lu: { lat: 49.82, lon: 6.13 },
  netherlands: { lat: 52.13, lon: 5.29 },
  nl: { lat: 52.13, lon: 5.29 },
  poland: { lat: 51.92, lon: 19.15 },
  pl: { lat: 51.92, lon: 19.15 },
  romania: { lat: 45.94, lon: 24.97 },
  ro: { lat: 45.94, lon: 24.97 },
  slovakia: { lat: 48.67, lon: 19.7 },
  sk: { lat: 48.67, lon: 19.7 },
  'united states': { lat: 37.09, lon: -95.71 },
  us: { lat: 37.09, lon: -95.71 },
}

function normKey(city: string, country: string): string {
  return `${city.trim().toLowerCase()}|${country.trim().toLowerCase()}`
}

function countryAliases(country: string): string[] {
  const c = country.trim()
  if (!c) return []
  const out = [c]
  const up = c.toUpperCase()
  if (ISO_TO_NAME[up]) out.push(ISO_TO_NAME[up])
  if (ISO3_TO_NAME[up]) out.push(ISO3_TO_NAME[up])
  const low = c.toLowerCase()
  for (const [iso, name] of Object.entries(ISO_TO_NAME)) {
    if (name.toLowerCase() === low) out.push(iso)
  }
  for (const [iso3, name] of Object.entries(ISO3_TO_NAME)) {
    if (name.toLowerCase() === low) out.push(iso3)
  }
  if (up === 'UK') out.push('GB')
  return [...new Set(out)]
}

function jitter(city: string, lat: number, lon: number): { lat: number; lon: number } {
  let h = 0
  for (let i = 0; i < city.length; i++) h = (h * 31 + city.charCodeAt(i)) >>> 0
  const angle = ((h % 360) * Math.PI) / 180
  const radius = 0.15 + (h % 80) / 400
  return {
    lat: Math.round((lat + radius * Math.cos(angle)) * 1e6) / 1e6,
    lon: Math.round((lon + radius * Math.sin(angle)) * 1e6) / 1e6,
  }
}

export function coerceGeoCoord(v: unknown): number | null {
  if (typeof v === 'number' && !Number.isNaN(v)) return v
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    return Number.isNaN(n) ? null : n
  }
  return null
}

export function resolveCityCoords(
  city: string,
  country: string,
): { lat: number; lon: number; source: string } | null {
  const cityS = city.trim()
  if (!cityS) return null

  for (const co of countryAliases(country)) {
    const seed = CITY_SEED[normKey(cityS, co)]
    if (seed) return { ...seed, source: 'seed' }
  }

  for (const alias of countryAliases(country)) {
    const cent = COUNTRY_CENTROIDS[alias.toLowerCase()]
    if (cent) {
      const { lat, lon } = jitter(cityS, cent.lat, cent.lon)
      return { lat, lon, source: 'country_centroid' }
    }
  }
  return null
}

export function enrichGeoLocation(loc: SalesGeoLocation): SalesGeoLocation {
  const lat = coerceGeoCoord(loc.lat)
  const lon = coerceGeoCoord(loc.lon)
  if (lat != null && lon != null) {
    return { ...loc, lat, lon }
  }
  const resolved = resolveCityCoords(loc.city, loc.country)
  if (!resolved) return { ...loc, lat: lat ?? null, lon: lon ?? null }
  return {
    ...loc,
    lat: resolved.lat,
    lon: resolved.lon,
    geo_source: loc.geo_source ?? resolved.source,
  }
}

export function enrichGeoLocations(locations: SalesGeoLocation[]): SalesGeoLocation[] {
  return locations.map(enrichGeoLocation)
}

export function hasMapCoords(loc: SalesGeoLocation): boolean {
  return coerceGeoCoord(loc.lat) != null && coerceGeoCoord(loc.lon) != null
}
