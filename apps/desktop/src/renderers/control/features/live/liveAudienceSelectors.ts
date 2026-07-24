import type {
  BackendAudienceSnapshot,
  BackendViewerSnapshot
} from '../../../../shared/contracts'

export type LiveAudienceFilter = 'active' | 'muted' | 'all'

export function selectLiveAudienceViewers(
  audience: BackendAudienceSnapshot | null,
  filter: LiveAudienceFilter,
  now: number
): readonly BackendViewerSnapshot[] {
  const viewers = audience?.viewers ?? []
  if (filter === 'muted') {
    return viewers.filter((viewer) => (viewer.muted_until_ms ?? 0) > now)
  }
  if (filter === 'active') {
    return viewers.filter((viewer) => viewer.presence_state === 'active')
  }
  return viewers.filter(
    (viewer) =>
      viewer.joined_at_ms !== null &&
      viewer.presence_state !== 'removed' &&
      viewer.presence_state !== 'ended'
  )
}
