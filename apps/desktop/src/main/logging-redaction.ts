const SENSITIVE_KEY_PATTERN =
  /api[_-]?key|authorization|cookie|credential|local[_-]?token|password|prompt|secret|token/i

const INLINE_SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, 'Bearer [REDACTED]'],
  [/\bsk-[A-Za-z0-9_-]{8,}\b/g, '[REDACTED_SECRET]'],
  [
    /\b(authorization)(["']?)(\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\r\n,;}]+)/gi,
    '$1$2$3[REDACTED]'
  ],
  [
    /\b(api[_-]?key|cookie|credential|local[_-]?token|password|prompt|secret|token)(["']?)(\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)/gi,
    '$1$2$3[REDACTED]'
  ],
  [/(https?:\/\/)[^/@\s]+@/gi, '$1[REDACTED]@']
]

export function redactLogText(value: string): string {
  return INLINE_SECRET_PATTERNS.reduce(
    (redacted, [pattern, replacement]) => redacted.replace(pattern, replacement),
    value
  )
}

export function redactLogData(data: readonly unknown[]): unknown[] {
  const seen = new WeakSet<object>()
  return data.map((value) => redactLogValue(value, seen, 0))
}

function redactLogValue(value: unknown, seen: WeakSet<object>, depth: number): unknown {
  if (typeof value === 'string') return redactLogText(value)
  if (value === null || typeof value !== 'object') return value
  if (Buffer.isBuffer(value) || value instanceof ArrayBuffer || ArrayBuffer.isView(value)) {
    const byteLength = value.byteLength
    return `[REDACTED_BINARY ${byteLength} bytes]`
  }
  if (value instanceof Error) {
    return {
      name: value.name,
      message: redactLogText(value.message),
      stack: value.stack ? redactLogText(value.stack) : undefined
    }
  }
  if (depth >= 4) return '[TRUNCATED]'
  if (seen.has(value)) return '[CIRCULAR]'

  if (Array.isArray(value)) {
    seen.add(value)
    const redacted = value.map((item) => redactLogValue(item, seen, depth + 1))
    seen.delete(value)
    return redacted
  }

  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) {
    return `[REDACTED_OBJECT ${value.constructor?.name ?? 'unknown'}]`
  }

  seen.add(value)
  const redacted = Object.fromEntries(
    Object.entries(value).map(([key, nestedValue]) => [
      key,
      SENSITIVE_KEY_PATTERN.test(key)
        ? '[REDACTED]'
        : redactLogValue(nestedValue, seen, depth + 1)
    ])
  )
  seen.delete(value)
  return redacted
}
