/**
 * Capitalize the first letter at the beginning of each sentence-like segment.
 * Handles starts after line breaks and punctuation (., !, ?, :, ;).
 */
export function capitalizeSentenceStarts(text: string | null | undefined): string {
  const input = String(text ?? '')
  if (!input) return input
  return input.replace(/(^|[\r\n]+|[.!?:;]\s+)([a-zäöü])/g, (_m, p1: string, p2: string) => {
    return `${p1}${p2.toUpperCase()}`
  })
}
