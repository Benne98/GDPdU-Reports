import { useEffect, useState } from 'react'

import { api } from '../lib/api'

/** Which optional reporting sub-pages currently have data (conditional display). */
export type ReportingAvailability = {
  /** Profitability needs a customer AND supplier master (partner-level margins). */
  profitability: boolean
  payroll: boolean
  fixed_assets: boolean
  /** OPOS sides reported separately — either may be loaded on its own. */
  receivables_aging: boolean
  payables_aging: boolean
  /** Legacy convenience = receivables OR payables. */
  opos: boolean
  /** True once the probe has resolved (success OR failure) — before that, treat the
   *  conditional tabs as hidden so they never flash in and then vanish. */
  loaded: boolean
}

const HIDDEN: ReportingAvailability = {
  profitability: false,
  payroll: false,
  fixed_assets: false,
  receivables_aging: false,
  payables_aging: false,
  opos: false,
  loaded: false,
}

// Module-level cache + in-flight promise so the availability probe runs ONCE per
// app session and every consumer (header nav + statement pages) shares the result.
let cached: ReportingAvailability | null = null
let inflight: Promise<ReportingAvailability> | null = null
const subscribers = new Set<(a: ReportingAvailability) => void>()

async function fetchAvailability(): Promise<ReportingAvailability> {
  if (cached) return cached
  if (!inflight) {
    inflight = api
      .reportingAvailability()
      .then(r => ({
        profitability: Boolean(r.profitability),
        payroll: Boolean(r.payroll),
        fixed_assets: Boolean(r.fixed_assets),
        receivables_aging: Boolean(r.receivables_aging),
        payables_aging: Boolean(r.payables_aging),
        opos: Boolean(r.opos),
        loaded: true,
      }))
      // On a probe failure keep everything hidden but mark loaded, so the always-on
      // tabs (GL + statements) still work and we do not block the UI.
      .catch<ReportingAvailability>(() => ({ ...HIDDEN, loaded: true }))
      .then(result => {
        cached = result
        inflight = null
        subscribers.forEach(fn => fn(result))
        return result
      })
  }
  return inflight
}

/** Read the shared reporting-availability probe (fetched once, cached app-wide). */
export function useReportingAvailability(): ReportingAvailability {
  const [state, setState] = useState<ReportingAvailability>(cached ?? HIDDEN)

  useEffect(() => {
    if (cached) {
      setState(cached)
      return
    }
    let active = true
    const sub = (a: ReportingAvailability) => {
      if (active) setState(a)
    }
    subscribers.add(sub)
    void fetchAvailability()
    return () => {
      active = false
      subscribers.delete(sub)
    }
  }, [])

  return state
}
