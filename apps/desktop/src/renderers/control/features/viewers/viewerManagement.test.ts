import { describe, expect, it } from 'vitest'
import type {
  BackendAudienceSnapshot,
  BackendViewerSnapshot
} from '../../../../shared/contracts'
import { selectViewers, summarizeViewers } from './viewerManagement'

const now = 10_000

function viewer(
  id: string,
  state: BackendViewerSnapshot['presence_state'],
  overrides: Partial<BackendViewerSnapshot> = {}
): BackendViewerSnapshot {
  return {
    viewer_instance_id: id,
    username: id,
    display_name: id,
    avatar_seed: id,
    color_seed: id,
    persona_id: 'persona',
    persona_display_name: '冷静分析员',
    presence_state: state,
    joined_at_ms: state === 'not_joined' ? null : 1_000,
    last_left_at_ms: state === 'left' ? 9_000 : null,
    join_count: state === 'not_joined' ? 0 : 1,
    muted_until_ms: null,
    viewer_sequence: 1,
    presence_revision: 1,
    moderation_revision: 1,
    ...overrides
  }
}

const audience: BackendAudienceSnapshot = {
  session_id: 'session-1',
  room_id: 'room-1',
  audience_epoch: 1,
  population_revision: 1,
  target_concurrent_viewers: 4,
  active_count: 2,
  viewers: [
    viewer('普通观众', 'active'),
    viewer('禁言观众', 'active', { muted_until_ms: 20_000, joined_at_ms: 2_000 }),
    viewer('暂离观众', 'left'),
    viewer('已踢观众', 'kicked'),
    viewer('候选身份', 'not_joined'),
    viewer('结束身份', 'ended')
  ]
}

describe('viewer management selectors', () => {
  it('counts only viewers who joined the current session', () => {
    expect(summarizeViewers(audience, now)).toEqual({
      active: 2,
      muted: 1,
      left: 1,
      total: 4
    })
  })

  it('filters muted viewers and excludes unjoined identities', () => {
    expect(selectViewers(audience, 'muted', '', now).map((item) => item.display_name)).toEqual([
      '禁言观众'
    ])
    expect(selectViewers(audience, 'all', '', now)).toHaveLength(4)
  })

  it('searches viewer identity and persona labels', () => {
    expect(selectViewers(audience, 'all', '普通', now).map((item) => item.username)).toEqual([
      '普通观众'
    ])
    expect(selectViewers(audience, 'all', '分析员', now)).toHaveLength(4)
  })
})
