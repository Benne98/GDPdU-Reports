/** Strip trailing legal-form suffixes from company names for compact tables. */
const LEGAL_SUFFIX_RE =
  /\s+(?:GmbH\s*&\s*Co\.\s*KG|GmbH\s*&\s*Co\.|GmbH|AG|KG|OHG|UG(?:\s*\(haftungsbeschränkt\))?|SE|e\.?\s*V\.?|Ltd\.?|Inc\.?|Corp\.?|Co\.\s*KG)\s*$/i

export function stripLegalForm(name: string): string {
  let s = (name ?? '').trim()
  if (!s) return s
  let prev = ''
  while (s !== prev) {
    prev = s
    s = s.replace(LEGAL_SUFFIX_RE, '').trim()
  }
  return s || name.trim()
}
