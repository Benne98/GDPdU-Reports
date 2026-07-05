import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type PayablesGeoCountryLocation,
  type PayablesGeoRow,
  type SalesGeoCountry,
} from '../../../../lib/api'
import { countryDisplayName } from '../../../../lib/countryLabels'
import { fmtAmount, fmtChartKpi } from '../../../../lib/fmt'
import { enrichGeoLocations, hasMapCoords } from '../../../../lib/salesGeoCoords'
import WorldMap from '../../WorldMap'
import SalesLocationBubbleMap, { type AgingGeoLocation } from '../../SalesLocationBubbleMap'

function toAgingGeoLocations(locs: PayablesGeoCountryLocation[]): AgingGeoLocation[] {
  const base = locs.map(l => ({
    city: l.city,
    country: l.country,
    postal_code: l.postal_code ?? null,
    revenue_keur: l.revenue_keur,
    customer_count: l.customer_count,
    lat: l.lat ?? null,
    lon: l.lon ?? null,
    geo_source: l.geo_source ?? null,
    before_due_keur: l.before_due_keur,
    overdue_keur: l.overdue_keur,
  }))
  return enrichGeoLocations(base).map((loc, i) => ({
    ...loc,
    before_due_keur: base[i]?.before_due_keur,
    overdue_keur: base[i]?.overdue_keur,
  }))
}

function toWorldMapRows(rows: PayablesGeoRow[]): SalesGeoCountry[] {
  return rows
    .filter(r => !!r.country)   // skip rows without a country_code (pre-OPOS rows)
    .map(r => ({
      country: countryDisplayName(r.country),
      revenue_keur: Math.round((r.balance / 1000) * 100) / 100,
      py_revenue_keur: 0,
      delta_keur: 0,
      gross_margin_pct: r.overdue_pct,
    }))
}

export default function PayablesGeographySection({
  year,
  month,
  entity,
  rows,
  loading,
}: {
  year: number
  month: number
  entity?: string
  rows: PayablesGeoRow[]
  loading?: boolean
}) {
  const [selectedCountryCode, setSelectedCountryCode] = useState<string | null>(null)
  const [countryLocs, setCountryLocs] = useState<{
    locations: AgingGeoLocation[]
    total_count: number
    mapped_count: number
  } | null>(null)
  const [locLoading, setLocLoading] = useState(false)

  const rowByCode = useMemo(() => {
    const m = new Map<string, PayablesGeoRow>()
    for (const r of rows) m.set(r.country, r)
    return m
  }, [rows])

  const displayToCode = useMemo(() => {
    const m = new Map<string, string>()
    for (const r of rows) m.set(countryDisplayName(r.country), r.country)
    return m
  }, [rows])

  const worldMapData = useMemo(() => toWorldMapRows(rows), [rows])
  const selectedDisplayName = selectedCountryCode ? countryDisplayName(selectedCountryCode) : null

  const tooltipExtraRows = useMemo(() => {
    return (bar: SalesGeoCountry) => {
      const code = displayToCode.get(bar.country)
      const r = code ? rowByCode.get(code) : undefined
      if (!r) return []
      return [
        { label: 'Not yet due', value: `${fmtChartKpi(r.before_due / 1000)} kEUR`, color: '#93C5FD' },
        { label: 'Overdue', value: `${fmtChartKpi(r.overdue / 1000)} kEUR`, color: '#FCD34D' },
        { label: 'Overdue %', value: `${r.overdue_pct.toLocaleString('de-DE', { maximumFractionDigits: 1 })}%` },
      ]
    }
  }, [displayToCode, rowByCode])

  useEffect(() => {
    if (!selectedCountryCode) {
      setCountryLocs(null)
      return
    }
    let ok = true
    setLocLoading(true)
    api
      .salesPayablesGeoCountryLocations(selectedCountryCode, year, month, entity)
      .then(res => {
        if (!ok) return
        const rawLocs = res.locations ?? []
        const locations = toAgingGeoLocations(rawLocs)
        setCountryLocs({
          locations,
          total_count: res.total_count ?? locations.length,
          mapped_count: locations.filter(hasMapCoords).length,
        })
      })
      .catch(() => ok && setCountryLocs(null))
      .finally(() => ok && setLocLoading(false))
    return () => {
      ok = false
    }
  }, [selectedCountryCode, year, month, entity])

  return (
    <div className="rounded-xl p-5" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}>
      <h3 className="text-sm font-semibold mb-1" style={{ color: '#1E3A5F' }}>
        Open payables by country
      </h3>
      <p className="text-xs mb-4" style={{ color: '#94A3B8' }}>
        Click a country bar to drill into supplier location map. Bubble size is open balance; colour reflects overdue share.
      </p>
      <div className="min-h-[560px]">
        <WorldMap
          data={worldMapData}
          loading={loading ?? false}
          selectedCountry={selectedDisplayName}
          onCountryClick={displayName => {
            const code = displayToCode.get(displayName)
            if (!code) return
            setSelectedCountryCode(prev => (prev === code ? null : code))
          }}
          valueLabel="Open payables"
          showGrossMargin={false}
          showYoY={false}
          tooltipExtraRows={tooltipExtraRows}
          emptyMessage="No open payables by country for this period"
        />
      </div>
      {selectedDisplayName && selectedCountryCode && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold mb-1" style={{ color: '#1E3A5F' }}>
            Locations in {selectedDisplayName}
            {countryLocs && countryLocs.total_count > 0 ? ` (${countryLocs.mapped_count}/${countryLocs.total_count} on map)` : ''}
          </h4>
          {rowByCode.get(selectedCountryCode) && (
            <p className="text-[10px] mb-2" style={{ color: '#64748B' }}>
              {fmtAmount(rowByCode.get(selectedCountryCode)!.before_due)} not yet due · {fmtAmount(rowByCode.get(selectedCountryCode)!.overdue)} overdue
            </p>
          )}
          <SalesLocationBubbleMap
            country={selectedDisplayName}
            locations={countryLocs?.locations ?? []}
            totalCount={countryLocs?.total_count ?? 0}
            loading={locLoading}
            valueLabel="Open payables"
            countLabel="supplier(s)"
            agingSplit
            emptyNoLocations={`No supplier locations with open payables in ${selectedDisplayName}.`}
            emptyNoCoords={`No mappable locations for ${selectedDisplayName}. Check city and country on supplier master data.`}
          />
        </div>
      )}
    </div>
  )
}
