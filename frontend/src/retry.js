export async function retryUntilResolved(operation, options = {}) {
  const attempts = options.attempts ?? 3
  const delayMs = options.delayMs ?? 1000

  let lastError
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await operation()
    } catch (error) {
      lastError = error
      if (attempt === attempts) break
      if (delayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, delayMs))
      }
    }
  }

  throw lastError
}
