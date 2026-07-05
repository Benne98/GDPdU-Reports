// TODO(remove after 5177 acceptance): drop the v4 gate
// `merged` mode (5180) adopts the v4 project-setup / group-GL ingestion path in the unified stack.
export const IS_DATA_UPDATE_V4 =
  import.meta.env.MODE === 'v4' || import.meta.env.MODE === 'merged';

/** Returns the v4 label when on 5178, else the exact legacy literal (byte-identical). */
export const v4Label = (legacy: string, v4: string): string =>
  IS_DATA_UPDATE_V4 ? v4 : legacy;
