// TODO(un-gate after 5177 acceptance): remove this flag and inline OverviewPageV2 as the default.
export const IS_OVERVIEW_V2 =
  import.meta.env.MODE === 'reporting-v2' ||
  import.meta.env.MODE === 'reporting-v2-sandbox';
