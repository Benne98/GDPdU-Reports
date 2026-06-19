/** Fixed pixel sizes — ResponsiveContainer needs explicit dimensions in narrow grid columns. */
export function agingDonutSize(compact: boolean) {
  return compact
    ? { width: 220, height: 180, inner: 46, outer: 70 }
    : { width: 280, height: 260, inner: 72, outer: 108 }
}

export function agingStatusDonutSize(compact: boolean) {
  return compact
    ? { width: 220, height: 170, inner: 42, outer: 66 }
    : { width: 280, height: 200, inner: 52, outer: 78 }
}
