/** Mark-step definitions driven by interpretation answers (sign_mode, value_type, layout). */

export type ColumnRole =
  | 'account'
  | 'account_description'
  | 'opening_balance'
  | 'eb_sh'
  | 'eb_s'
  | 'eb_h'
  | 'amount'
  | 'sh'
  | 'period1_amount'
  | 'period1_sh'
  | 'period2_amount'
  | 'period2_sh'
  | 'period1_s'
  | 'period1_h'
  | 'period2_s'
  | 'period2_h'

export type MarkStep = {
  role: ColumnRole
  label: string
  skippable?: boolean
}

function appendOpeningBalanceSteps(steps: MarkStep[], signMode: string): void {
  steps.push({
    role: 'opening_balance',
    label: 'Opening balance amount',
    skippable: true,
  })
  if (signMode === 'sh_column') {
    steps.push({
      role: 'eb_sh',
      label: 'Opening balance debit/credit indicator column',
      skippable: true,
    })
  } else if (signMode === 'sh_two_columns') {
    steps.push({
      role: 'eb_s',
      label: 'Opening balance — debit (S) indicator column',
      skippable: true,
    })
    steps.push({
      role: 'eb_h',
      label: 'Opening balance — credit (H) indicator column',
      skippable: true,
    })
  }
}

export function buildMarkSteps(
  signMode: string,
  _valueType: string,
  isSingleSheet: boolean,
): MarkStep[] {
  const steps: MarkStep[] = [
    { role: 'account', label: 'Account number' },
    { role: 'account_description', label: 'Description' },
  ]

  appendOpeningBalanceSteps(steps, signMode)

  if (signMode === 'already_signed') {
    if (isSingleSheet) {
      steps.push({ role: 'period1_amount', label: 'Period column 1' })
      steps.push({ role: 'period2_amount', label: 'Period column 2' })
    } else {
      steps.push({ role: 'amount', label: 'Period amount column' })
    }
    return steps
  }

  if (signMode === 'sh_column') {
    if (isSingleSheet) {
      steps.push({ role: 'period1_amount', label: 'Period column 1' })
      steps.push({
        role: 'period1_sh',
        label: 'Debit/credit indicator for period column 1',
      })
      steps.push({ role: 'period2_amount', label: 'Period column 2' })
      steps.push({
        role: 'period2_sh',
        label: 'Debit/credit indicator for period column 2',
      })
    } else {
      steps.push({ role: 'amount', label: 'Period amount column' })
      steps.push({ role: 'sh', label: 'Period debit/credit indicator column' })
    }
    return steps
  }

  if (signMode === 'sh_two_columns') {
    if (isSingleSheet) {
      steps.push({ role: 'period1_amount', label: 'Period column 1' })
      steps.push({
        role: 'period1_s',
        label: 'Debit (S) indicator column for period 1',
      })
      steps.push({
        role: 'period1_h',
        label: 'Credit (H) indicator column for period 1',
      })
      steps.push({ role: 'period2_amount', label: 'Period column 2' })
      steps.push({
        role: 'period2_s',
        label: 'Debit (S) indicator column for period 2',
      })
      steps.push({
        role: 'period2_h',
        label: 'Credit (H) indicator column for period 2',
      })
    } else {
      steps.push({ role: 'amount', label: 'Period amount column' })
      steps.push({ role: 'period1_s', label: 'Debit (S) indicator column' })
      steps.push({ role: 'period1_h', label: 'Credit (H) indicator column' })
    }
    return steps
  }

  steps.push({ role: 'amount', label: 'Period amount column' })
  return steps
}

/** Roles per repeating period block (output keys for SuSabyYear). */
export function blockRolesForSignMode(signMode: string): string[] {
  if (signMode === 'already_signed') return ['amount']
  if (signMode === 'sh_column') return ['amount', 'sh']
  if (signMode === 'sh_two_columns') return ['amount', 'sh_s', 'sh_h']
  return ['amount']
}

export function roleToBlockField(role: ColumnRole, _signMode: string): string {
  if (role === 'period1_sh' || role === 'period2_sh' || role === 'sh') return 'sh'
  if (role === 'period1_s' || role === 'period2_s') return 'sh_s'
  if (role === 'period1_h' || role === 'period2_h') return 'sh_h'
  if (role.includes('amount') || role === 'amount') return 'amount'
  return role
}

export function roleColor(role: ColumnRole): string {
  if (role === 'account') return '#64748B'
  if (role === 'account_description') return '#94A3B8'
  if (role === 'opening_balance') return '#9CA3AF'
  if (role.startsWith('eb_')) return '#A78BFA'
  if (role.includes('amount') || role === 'amount') return '#3B82F6'
  if (role.includes('_sh') || role === 'sh') return '#10B981'
  if (role.includes('_s') || role.endsWith('_s')) return '#22C55E'
  if (role.includes('_h') || role.endsWith('_h')) return '#EF4444'
  return '#94A3B8'
}
