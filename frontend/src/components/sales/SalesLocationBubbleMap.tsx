/**
 * Bubble map: circle diameter ∝ revenue at customer city locations.
 */
import { useMemo } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { SalesGeoLocation } from '../../lib/api'
import { fmtChartKpi } from '../../lib/fmt'
import { coerceGeoCoord, enrichGeoLocations, hasMapCoords } from '../../lib/salesGeoCoords'

export type AgingGeoLocation = SalesGeoLocation & {
  before_due_keur?: number
  overdue_keur?: number
}

interface Props {
  country: string
  locations: AgingGeoLocation[]
  totalCount?: number
  loading?: boolean
  valueLabel?: string
  countLabel?: string
  emptyNoLocations?: string
  emptyNoCoords?: string
  /** When set, bubble colour reflects overdue share and popup shows due / overdue split. */
  agingSplit?: boolean
}

export default function SalesLocationBubbleMap({
  country,
  locations,
  totalCount = 0,
  loading,
  valueLabel = 'Revenue',
  countLabel = 'customer(s)',
  emptyNoLocations,
  emptyNoCoords,
  agingSplit = false,
}: Props) {
  const enriched = useMemo(() => enrichGeoLocations(locations), [locations])

  const mapped = useMemo(
    (): Array<AgingGeoLocation & { lat: number; lon: number }> =>
      enriched
        .filter(hasMapCoords)
        .map(l => ({
          ...l,
          lat: coerceGeoCoord(l.lat)!,
          lon: coerceGeoCoord(l.lon)!,
        })),
    [enriched],
  )

  const maxRev = useMemo(
    () => Math.max(...mapped.map(l => l.revenue_keur), 1),
    [mapped],
  )

  const bubbleFill = (loc: (typeof mapped)[0]) => {
    if (!agingSplit) {
      return loc.geo_source === 'country_centroid' ? '#0F766E' : '#1E3A5F'
    }
    const total = (loc.before_due_keur ?? 0) + (loc.overdue_keur ?? 0)
    const odPct = total > 0 ? (loc.overdue_keur ?? 0) / total : 0
    if (odPct >= 0.35) return '#D97706'
    if (odPct >= 0.15) return '#F59E0B'
    return '#3B82F6'
  }

  const center = useMemo((): [number, number] => {
    if (mapped.length === 0) return [51.0, 10.0]
    const lat = mapped.reduce((s, l) => s + (l.lat ?? 0), 0) / mapped.length
    const lon = mapped.reduce((s, l) => s + (l.lon ?? 0), 0) / mapped.length
    return [lat, lon]
  }, [mapped])

  const radius = (rev: number) => {
    const t = Math.sqrt(rev / maxRev)
    return 8 + t * 42
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-xs" style={{ color: '#94A3B8' }}>
        Loading location map…
      </div>
    )
  }

  if (locations.length === 0 && totalCount === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-xs text-center px-4" style={{ color: '#94A3B8' }}>
        {emptyNoLocations ?? `No locations with ${valueLabel.toLowerCase()} in ${country} for this period.`}
      </div>
    )
  }

  if (mapped.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-xs text-center px-4" style={{ color: '#94A3B8' }}>
        {emptyNoCoords ?? `No mappable locations for ${country}. Check that city and country are set on records.`}
      </div>
    )
  }

  return (
    <div className="h-80 rounded-lg overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
      <MapContainer
        key={`${country}-${mapped.length}`}
        center={center}
        zoom={6}
        style={{ height: '100%', width: '100%' }}
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; OpenStreetMap'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {mapped.map(loc => (
          <CircleMarker
            key={`${loc.city}-${loc.postal_code ?? ''}-${loc.lat}`}
            center={[loc.lat!, loc.lon!]}
            radius={radius(loc.revenue_keur)}
            pathOptions={{
              color: bubbleFill(loc),
              fillColor: bubbleFill(loc),
              fillOpacity: 0.45,
              weight: 1,
            }}
          >
            <Popup>
              <div className="text-xs">
                <div className="font-semibold">{loc.city}{loc.postal_code ? ` (${loc.postal_code})` : ''}</div>
                <div>{fmtChartKpi(loc.revenue_keur)} kEUR open</div>
                {agingSplit && (loc.before_due_keur != null || loc.overdue_keur != null) && (
                  <>
                    <div style={{ color: '#3B82F6' }}>
                      Not yet due: {fmtChartKpi(loc.before_due_keur ?? 0)} kEUR
                    </div>
                    <div style={{ color: '#D97706' }}>
                      Overdue: {fmtChartKpi(loc.overdue_keur ?? 0)} kEUR
                    </div>
                  </>
                )}
                <div>{loc.customer_count} {countLabel}</div>
                {loc.geo_source === 'country_centroid' && (
                  <div className="text-slate-500 mt-1">Approximate position (country centroid)</div>
                )}
              </div>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}
