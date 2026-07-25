import { describe, expect, it } from 'vitest'
import type { AiCallTrace } from '../../../../shared/backend-client'
import {
  formatScreenChangeScore,
  formatViewerSelectionReasons,
  formatViewerTriggerLabels,
  formatViewerTriggerReasons
} from './aiCallFormatters'

const triggerContext: NonNullable<AiCallTrace['trigger_context']> = {
  triggers: ['user_text', 'screen_change'],
  trigger_event_ids: ['event-1'],
  trigger_frame_ids: ['frame-1'],
  screen_change_score: 0.73,
  target_viewer_id: null,
  target_persona_id: null,
  target_ambiguous: false,
  selection_reason_codes: ['per_viewer_independent_decision']
}

describe('AI call trigger context formatters', () => {
  it('formats the trigger type and source reason for a viewer request', () => {
    expect(formatViewerTriggerLabels(triggerContext.triggers)).toBe('主播/用户文本 + 画面变化')
    expect(formatViewerTriggerReasons(triggerContext)).toEqual([
      '收到主播/用户文本',
      '检测到画面变化，变化分数 0.73 (73%)'
    ])
    expect(formatScreenChangeScore(triggerContext.screen_change_score)).toBe('0.73 (73%)')
  })

  it('includes direct targeting in the audience scheduling reason', () => {
    expect(formatViewerSelectionReasons({
      ...triggerContext,
      target_viewer_id: 'viewer-42'
    })).toEqual(['定向观众：viewer-42', '独立观众调度'])
  })

  it('falls back for legacy trigger context without selection reasons', () => {
    expect(formatViewerSelectionReasons({
      ...triggerContext,
      selection_reason_codes: undefined
    })).toEqual(['本波触发后由观众调度器选中'])
  })
})
