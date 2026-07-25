export type AiCallImageReference = {
  previewId: string | null
  mimeType: string | null
  sha256: string | null
}

export function collectAiCallImageReferences(value: unknown): AiCallImageReference[] {
  const references: AiCallImageReference[] = []
  const previewIds = new Set<string>()
  const visited = new WeakSet<object>()

  function visit(candidate: unknown): void {
    if (!candidate || typeof candidate !== 'object') return
    if (visited.has(candidate)) return
    visited.add(candidate)

    if (Array.isArray(candidate)) {
      candidate.forEach(visit)
      return
    }

    const record = candidate as Record<string, unknown>
    if (record.type === 'media_ref') {
      const previewId = typeof record.preview_id === 'string' ? record.preview_id : null
      if (previewId === null || !previewIds.has(previewId)) {
        if (previewId !== null) previewIds.add(previewId)
        references.push({
          previewId,
          mimeType: typeof record.mime_type === 'string' ? record.mime_type : null,
          sha256: typeof record.sha256 === 'string' ? record.sha256 : null
        })
      }
    }
    Object.values(record).forEach(visit)
  }

  visit(value)
  return references
}
