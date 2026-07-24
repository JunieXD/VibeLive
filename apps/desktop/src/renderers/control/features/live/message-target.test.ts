import { describe, expect, it } from 'vitest'
import {
  buildRuntimeMessageTargets,
  insertSelectedTarget,
  parseTargetedMessage,
  selectionMatches,
  suggestMentionTargets,
  updateSelectedTarget
} from './message-target'

const targets = [
  {
    kind: 'viewer' as const,
    id: 'viewer:room:critic:01',
    label: '挑刺党·01',
    personaId: 'critic'
  },
  {
    kind: 'persona' as const,
    id: 'critic',
    label: '挑刺党'
  }
]

describe('targeted text messages', () => {
  it('extracts a Viewer mention into a structured target', () => {
    const selected = insertSelectedTarget('@挑刺党·', targets[0])
    expect(parseTargetedMessage(`${selected.message}这个操作怎么样？`, selected.selection)).toEqual({
      text: '这个操作怎么样？',
      targetViewerId: 'viewer:room:critic:01'
    })
  })

  it('extracts a Persona mention without sending the mention text', () => {
    const selected = insertSelectedTarget('@挑', targets[1])
    expect(parseTargetedMessage(`${selected.message}你们怎么看？`, selected.selection)).toEqual({
      text: '你们怎么看？',
      targetPersonaId: 'critic'
    })
  })

  it('keeps unknown mentions as ordinary text and filters autocomplete by label', () => {
    expect(parseTargetedMessage('@路人甲 你好', null)).toEqual({ text: '@路人甲 你好' })
    expect(suggestMentionTargets('问问 @挑', targets)).toEqual(targets)
  })

  it('keeps same-label Viewer and Persona targets distinct by the selected identity', () => {
    const viewer = { ...targets[0], label: '挑刺党' }
    const viewerSelection = insertSelectedTarget('问问 @挑', viewer)
    expect(parseTargetedMessage(`${viewerSelection.message}单独回答`, viewerSelection.selection)).toEqual({
      text: '问问 单独回答',
      targetViewerId: 'viewer:room:critic:01'
    })
    const personaSelection = insertSelectedTarget('@挑', targets[1])
    expect(parseTargetedMessage(`${personaSelection.message}一起回答`, personaSelection.selection)).toEqual({
      text: '一起回答',
      targetPersonaId: 'critic'
    })
    expect(selectionMatches(viewerSelection.message, viewerSelection.selection)).toBe(true)
  })

  it('removes only the selected trailing mention when duplicate labels exist', () => {
    const selected = insertSelectedTarget('@挑刺党 先问这个，再问 @挑', targets[1])
    const completeMessage = `${selected.message}最后回答`
    expect(parseTargetedMessage(completeMessage, selected.selection)).toEqual({
      text: '@挑刺党 先问这个，再问 最后回答',
      targetPersonaId: 'critic'
    })
    const removedTrailing = '@挑刺党 先问这个，再问 最后回答'
    expect(updateSelectedTarget(completeMessage, removedTrailing, selected.selection)).toBeNull()
    expect(parseTargetedMessage(removedTrailing, selected.selection)).toEqual({
      text: removedTrailing
    })
  })

  it('moves the selected range when preceding context changes and preserves surrounding text', () => {
    const selected = insertSelectedTarget('问问 @挑', targets[1])
    const previous = `${selected.message}前后都保留`
    const next = `先${previous}`
    const moved = updateSelectedTarget(previous, next, selected.selection)
    expect(moved).not.toBeNull()
    expect(parseTargetedMessage(next, moved)).toEqual({
      text: '先问问 前后都保留',
      targetPersonaId: 'critic'
    })
  })

  it('uses authoritative backend Viewer IDs and excludes removed instances', () => {
    const runtimeTargets = buildRuntimeMessageTargets([
      {
        viewer_instance_id: 'backend-viewer-7',
        room_id: 'room-1',
        session_id: 'session-1',
        audience_epoch: 2,
        persona_id: 'critic',
        persona_revision: 1,
        persona_content_hash: 'a'.repeat(64),
        ordinal: 7,
        username: 'critic-07',
        display_name: '挑刺党·07',
        avatar_seed: 'avatar-7',
        color_seed: 'color-7',
        locale: 'zh-CN',
        variant: {
          activity_baseline: 1,
          attention_span: 1,
          social_initiative: 1,
          reply_affinity: 1,
          expression_length: 1,
          skepticism: 1,
          encouragement: 0,
          meme_affinity: 0,
          focus: 'action',
          silence_tendency: 0,
          stay_duration_tendency: 1,
          rejoin_tendency: 0
        },
        viewer_sequence: 0,
        lifecycle_state: 'active',
        presence_revision: 1,
        moderation_revision: 1,
        behavior_revision: 1,
        join_count: 1,
        created_at_ms: 10
      },
      {
        viewer_instance_id: 'removed-viewer',
        room_id: 'room-1',
        session_id: 'session-1',
        audience_epoch: 1,
        persona_id: 'critic',
        persona_revision: 1,
        persona_content_hash: 'b'.repeat(64),
        ordinal: 1,
        username: 'old-viewer',
        display_name: '旧实例',
        avatar_seed: 'avatar-old',
        color_seed: 'color-old',
        locale: 'zh-CN',
        variant: {
          activity_baseline: 1,
          attention_span: 1,
          social_initiative: 1,
          reply_affinity: 1,
          expression_length: 1,
          skepticism: 1,
          encouragement: 0,
          meme_affinity: 0,
          focus: 'action',
          silence_tendency: 0,
          stay_duration_tendency: 1,
          rejoin_tendency: 0
        },
        viewer_sequence: 1,
        lifecycle_state: 'removed',
        presence_revision: 2,
        moderation_revision: 1,
        behavior_revision: 2,
        join_count: 1,
        created_at_ms: 1,
        removed_at_ms: 9
      }
    ], [{ id: 'critic', name: '挑刺党' }])

    expect(runtimeTargets).toEqual([
      {
        kind: 'viewer',
        id: 'backend-viewer-7',
        label: '挑刺党·07',
        personaId: 'critic'
      },
      { kind: 'persona', id: 'critic', label: '挑刺党' }
    ])
  })
})
