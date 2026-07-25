import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import {
  createInitialAudienceWorkspace,
  DEFAULT_VISUAL_SETTINGS
} from '../../../../shared/audience'
import {
  ModeToolbar,
  visualSettingsForBarrageGenerationMode
} from './ModeToolbar'

describe('ModeToolbar barrage generation mode', () => {
  it('applies the fixed visual policy when switching to window aggregation', () => {
    expect(
      visualSettingsForBarrageGenerationMode(DEFAULT_VISUAL_SETTINGS, 'window_batch')
    ).toMatchObject({
      barrageGenerationMode: 'window_batch',
      viewerVisualInputMode: 'direct_frames',
      frameBundleSize: 4,
      frameWindowMs: 30_000,
      frameSelectionStrategy: 'change_peaks',
      frameMaxDimension: 768,
      frameQuality: 0.7
    })
  })

  it('keeps the current frame policy when switching back to per-viewer generation', () => {
    const current = {
      ...DEFAULT_VISUAL_SETTINGS,
      barrageGenerationMode: 'window_batch' as const,
      frameBundleSize: 4,
      frameWindowMs: 25_000
    }

    expect(visualSettingsForBarrageGenerationMode(current, 'per_viewer')).toEqual({
      ...current,
      barrageGenerationMode: 'per_viewer'
    })
  })

  it('renders window aggregation controls without contradictory editable values', () => {
    const workspace = createInitialAudienceWorkspace()
    const activeMode = {
      ...workspace.modeState.modes[0],
      visualSettings: visualSettingsForBarrageGenerationMode(
        workspace.modeState.modes[0].visualSettings,
        'window_batch'
      )
    }
    const previousDocument = globalThis.document
    Object.defineProperty(globalThis, 'document', {
      configurable: true,
      value: { body: {} }
    })
    try {
      const markup = renderToStaticMarkup(createElement(ModeToolbar, {
        workspace,
        activeMode,
        structureLocked: false,
        modeNameDraft: activeMode.name,
        onModeNameDraftChange: () => undefined,
        onModeNameCommit: () => undefined,
        onSelectMode: () => undefined,
        onPatchMode: () => undefined,
        onDuplicateMode: () => undefined,
        onResetMode: () => undefined,
        onDeleteMode: () => undefined
      }))

      expect(markup).toContain('Real（30 秒窗口）')
      expect(markup).toMatch(/aria-label="视觉输入"[^>]*disabled/)
      expect(markup).toMatch(/aria-label="帧选择策略"[^>]*disabled/)
      expect(markup).toContain('type="number" min="1" max="5" disabled="" value="4"')
      expect(markup).toContain(
        'type="number" min="1" max="30000" step="500" disabled="" value="30000"'
      )
      expect(markup).toContain('发言调度')
      expect(markup).toContain('文本/语音/系统音频每波')
      expect(markup).toContain('画面变化每波')
      expect(markup).toContain('静默每波')
      expect(markup).toContain('运行限流')
      expect(markup).toContain('并发请求')
      expect(markup).toContain('队列容量')
    } finally {
      Object.defineProperty(globalThis, 'document', {
        configurable: true,
        value: previousDocument
      })
    }
  })
})
