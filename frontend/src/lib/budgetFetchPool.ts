/** Run async tasks with a bounded concurrency pool. */
export async function runWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) return [];
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const workers = Math.min(Math.max(1, concurrency), items.length);

  async function worker() {
    while (true) {
      const i = nextIndex;
      nextIndex += 1;
      if (i >= items.length) break;
      results[i] = await fn(items[i], i);
    }
  }

  await Promise.all(Array.from({ length: workers }, () => worker()));
  return results;
}
