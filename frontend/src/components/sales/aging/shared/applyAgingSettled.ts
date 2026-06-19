/** Apply Promise.allSettled result — never clear data on rejection. */
export function applyFulfilled<T>(
  result: PromiseSettledResult<T>,
  isCurrent: () => boolean,
  onValue: (value: T) => void,
) {
  if (!isCurrent() || result.status !== 'fulfilled') return
  onValue(result.value)
}
