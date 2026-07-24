import { describe, expect, it } from 'vitest'
import type {
  BackendAudienceSnapshot,
  BackendViewerSnapshot
} from '../../../../shared/contracts'
import { selectLiveAudienceViewers } from './liveAudienceSelectors'

const viewer: BackendViewerSnapshot = {
  viewer_instance_id: 'viewer-1',
  username: 'viewer-1',
  display_name: '观众一号',
  avatar_seed: 'avatar-1',
  color_seed: 'color-1',
  persona_id: 'curious',
  persona_display_name: '好奇观众',
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
  target_concurrent_viewers: 2,
  active_count: 1,
  viewers: [
    { ...viewer, muted_until_ms: 2_000 },
    {
      ...viewer,
      viewer_instance_id: 'viewer-2',
      username: 'viewer-2',
      display_name: '观众二号',
      presence_state: 'left',
      muted_until_ms: null
    }
  ]
}

describe('selectLiveAudienceViewers', () => {
  it('removes an expired viewer from the muted filter', () => {
    expect(selectLiveAudienceViewers(audience, 'muted', 1_999)).toHaveLength(1)
    expect(selectLiveAudienceViewers(audience, 'muted', 2_000)).toHaveLength(0)
  })

  it('keeps active and historical filters independent', () => {
    expect(selectLiveAudienceViewers(audience, 'active', 2_000)).toHaveLength(1)
    expect(selectLiveAudienceViewers(audience, 'all', 2_000)).toHaveLength(2)
  })
})
