import type {
  AiCallQuery,
  AiCallQueryResponse,
  AiCallTrace
} from '../../../../shared/backend-client'

export type AiCallPageLoader = (
  query: AiCallQuery
) => Promise<AiCallQueryResponse>

export async function queryAllAiCalls(
  query: AiCallQuery,
  loadPage: AiCallPageLoader
): Promise<AiCallTrace[]> {
  const items: AiCallTrace[] = []
  const seenCallIds = new Set<string>()
  const seenCursors = new Set<string>()
  let cursor = query.cursor

  for (;;) {
    const page = await loadPage({
      ...query,
      cursor,
      limit: query.limit ?? 250
    })
    for (const item of page.items) {
      if (seenCallIds.has(item.call_id)) continue
      seenCallIds.add(item.call_id)
      items.push(item)
    }
    const nextCursor = page.next_cursor ?? undefined
    if (!nextCursor) return items
    if (seenCursors.has(nextCursor)) {
      throw new Error('AI 调用分页游标重复。')
    }
    seenCursors.add(nextCursor)
    cursor = nextCursor
  }
}
