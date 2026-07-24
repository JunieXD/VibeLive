import { describe, expect, it } from 'vitest'
import type {
  BackendAudienceSnapshot,
  BackendViewerSnapshot
} from '../../../shared/contracts'
import { mergeViewerSnapshot } from './useLiveAudience'

const viewer: BackendViewerSnapshot = {
  viewer_instance_id: 'viewer-1',
  username: 'pixel-user',
  display_name: 'pixel-user',
  avatar_seed: 'avatar-1',
  color_seed: 'color-1',
  persona_id: 'curious',
  persona_display_name: 'Curious',
  presence_state: 'active',
  joined_at_ms: 100,
  last_left_at_ms: null,
  join_count: 1,
  muted_until_ms: null,
  viewer_sequence: 0,
  presence_revision: 1,
  moderation_revision: 1
}

const audience: BackendAudienceSnapshot = {
  session_id: 'session-1',
  room_id: 'room-1',
  audience_epoch: 1,
  population_revision: 1,
  target_concurrent_viewers: 1,
  active_count: 1,
  viewers: [viewer]
}

describe('mergeViewerSnapshot', () => {
  it('updates the active count when a command changes presence', () => {
    const kicked = mergeViewerSnapshot(
      audience,
      { ...viewer, presence_state: 'kicked', presence_revision: 2 },
      2
    )

    expect(kicked.population_revision).toBe(2)
    expect(kicked.active_count).toBe(0)
    expect(kicked.viewers).toHaveLength(1)
    expect(kicked.viewers[0].presence_state).toBe('kicked')
  })

  it('adds a newly joined replacement exactly once', () => {
    const replacement = { ...viewer, viewer_instance_id: 'viewer-2' }
    const joined = mergeViewerSnapshot(audience, replacement, 2)
    const repeated = mergeViewerSnapshot(joined, replacement, 2)

    expect(joined.active_count).toBe(2)
    expect(repeated.viewers).toHaveLength(2)
  })

  it('does not let a late command response regress newer Viewer state', () => {
    const unmuted = mergeViewerSnapshot(
      audience,
      { ...viewer, moderation_revision: 3 },
      3
    )
    const lateMuteResponse = {
      ...viewer,
      muted_until_ms: 5_000,
      moderation_revision: 2
    }

    const merged = mergeViewerSnapshot(unmuted, lateMuteResponse)

    expect(merged.population_revision).toBe(3)
    expect(merged.viewers[0].muted_until_ms).toBeNull()
    expect(merged.viewers[0].moderation_revision).toBe(3)
  })

  it('advances population revision even when an event carries stale Viewer state', () => {
    const current = mergeViewerSnapshot(
      audience,
      { ...viewer, presence_revision: 3, presence_state: 'kicked' },
      3
    )

    const merged = mergeViewerSnapshot(
      current,
      { ...viewer, presence_revision: 2, presence_state: 'active' },
      4
    )

    expect(merged.population_revision).toBe(4)
    expect(merged.active_count).toBe(0)
    expect(merged.viewers[0].presence_state).toBe('kicked')
  })
})
