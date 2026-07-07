// TODO(un-gate after 5177 acceptance): remove this flag and inline OverviewPageV2 as the default.
// `merged` mode (5180) turns the reporting-v2 experience on in the unified stack; because Vite
// inlines MODE at build time, this OR is dead-code-eliminated (inert) in every other bundle.
export const IS_OVERVIEW_V2 =
  import.meta.env.MODE === 'reporting-v2' ||
  import.meta.env.MODE === 'reporting-v2-sandbox' ||
  import.meta.env.MODE === 'merged' ||
  import.meta.env.MODE === 'v5';
