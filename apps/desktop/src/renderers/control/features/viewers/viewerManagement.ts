import type {
  BackendAudienceSnapshot,
  BackendViewerSnapshot,
  ViewerPresenceState
} from '../../../../shared/contracts'

export type ViewerFilter = 'all' | 'active' | 'muted' | 'left' | 'kicked'

export type ViewerSummary = {
  active: number
  muted: number
  left: number
  total: number
}

export function isSessionViewer(viewer: BackendViewerSnapshot): boolean {
  return (
    viewer.joined_at_ms !== null &&
    viewer.presence_state !== 'not_joined' &&
    viewer.presence_state !== 'ended' &&
    viewer.presence_state !== 'removed'
  )
}

export function isViewerMuted(viewer: BackendViewerSnapshot, now: number): boolean {
  return viewer.presence_state === 'active' && (viewer.muted_until_ms ?? 0) > now
}

export function summarizeViewers(
  audience: BackendAudienceSnapshot | null,
  now: number
): ViewerSummary {
  const viewers = (audience?.viewers ?? []).filter(isSessionViewer)
  return {
    active: viewers.filter((viewer) => viewer.presence_state === 'active').length,
    muted: viewers.filter((viewer) => isViewerMuted(viewer, now)).length,
    left: viewers.filter((viewer) => viewer.presence_state === 'left').length,
    total: viewers.length
  }
}

const statePriority: Record<ViewerPresenceState, number> = {
  active: 0,
  left: 1,
  kicked: 2,
  not_joined: 3,
  ended: 4,
  removed: 5
}

export function selectViewers(
  audience: BackendAudienceSnapshot | null,
  filter: ViewerFilter,
  query: string,
  now: number
): BackendViewerSnapshot[] {
  const normalizedQuery = query.trim().toLocaleLowerCase()
  return (audience?.viewers ?? [])
    .filter(isSessionViewer)
    .filter((viewer) => {
      if (filter === 'muted') return isViewerMuted(viewer, now)
      if (filter === 'all') return true
      return viewer.presence_state === filter
    })
    .filter((viewer) => {
      if (!normalizedQuery) return true
      return [viewer.display_name, viewer.username, viewer.persona_display_name].some((value) =>
        value.toLocaleLowerCase().includes(normalizedQuery)
      )
    })
    .sort((left, right) => {
      const stateDifference =
        statePriority[left.presence_state] - statePriority[right.presence_state]
      if (stateDifference !== 0) return stateDifference
      return (right.joined_at_ms ?? 0) - (left.joined_at_ms ?? 0)
    })
}
