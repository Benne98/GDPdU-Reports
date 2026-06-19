import StatementsPage from './StatementsPage'

/** Working capital — WC statement, timeline, and DSO/DIO/DPO/CCC KPIs. */
export default function WorkingCapitalPage() {
  return (
    <StatementsPage
      statement="wc"
      kicker="Working capital"
      title="Working capital"
      description="Trade and other working capital with timeline and DSO/DIO/DPO/CCC for {period}. Click a value to drill into GL lines."
    />
  )
}
