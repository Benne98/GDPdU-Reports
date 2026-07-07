/** True for the independent sandbox stack (5179) and the unified `merged` stack (5180). */
export const IS_REPORTING_V2_SANDBOX =
  import.meta.env.MODE === 'reporting-v2-sandbox' || import.meta.env.MODE === 'merged' || import.meta.env.MODE === 'v5';
