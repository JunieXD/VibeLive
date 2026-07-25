import type { AiCallListItem } from '../../../../shared/backend-client'

export function mergeAiCallFirstPage(
  current: readonly AiCallListItem[],
  firstPage: readonly AiCallListItem[]
): AiCallListItem[] {
  const refreshedCallIds = new Set(firstPage.map((item) => item.call_id))
  return [...firstPage, ...current.filter((item) => !refreshedCallIds.has(item.call_id))]
}

export function appendAiCallPage(
  current: readonly AiCallListItem[],
  nextPage: readonly AiCallListItem[]
): AiCallListItem[] {
  const knownCallIds = new Set(current.map((item) => item.call_id))
  return [...current, ...nextPage.filter((item) => !knownCallIds.has(item.call_id))]
}
