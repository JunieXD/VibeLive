import { createElement, createRef } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { DEFAULT_VISUAL_SETTINGS } from '../../visual'
import { LiveChat } from './LiveChat'
import { LiveMixer } from './LiveMixer'

describe('dual audio UI', () => {
  it('renders independent microphone and system audio status rows', () => {
    const markup = renderToStaticMarkup(
      createElement(LiveMixer, {
        captureStream: null,
        cameraStream: null,
        captureStatus: '预览中',
        cameraStatus: '已关闭',
        microphoneLevel: 32,
        microphoneStatus: '正常',
        systemAudioLevel: 18,
        systemAudioReady: true,
        systemAudioStatus: '正常',
        microphonePartial: '主播正在说话',
        systemAudioPartial: '队友正在说话',
        asrReady: true,
        asrStatus: '已就绪',
        visualSettings: DEFAULT_VISUAL_SETTINGS,
        lastFrameBytes: null,
        lastFrameOverTarget: false,
        lastVisualBatchAt: null,
        visualPipelineStatus: 'local-preview'
      })
    )

    expect(markup).toContain('麦克风音量 32%')
    expect(markup).toContain('麦克风（主播）')
    expect(markup).toContain('系统声音音量 18%')
    expect(markup).toContain('主播正在说话')
    expect(markup).toContain('队友正在说话')
  })

  it('keeps final transcript labels visible in room activity', () => {
    const markup = renderToStaticMarkup(
      createElement(LiveChat, {
        chatListRef: createRef<HTMLDivElement>(),
        activity: [
          {
            id: 'mic-final',
            source: 'transcript',
            author: '麦克风（主播）',
            text: '主播发言'
          },
          {
            id: 'system-final',
            source: 'transcript',
            author: '系统声音',
            text: '队友发言'
          }
        ]
      })
    )

    expect(markup).toContain('麦克风（主播）')
    expect(markup).toContain('系统声音')
    expect(markup).toContain('主播发言')
    expect(markup).toContain('队友发言')
  })

  it('shows transport failure ahead of a stale partial transcript', () => {
    const markup = renderToStaticMarkup(
      createElement(LiveMixer, {
        captureStream: null,
        cameraStream: null,
        captureStatus: '预览中',
        cameraStatus: '已关闭',
        microphoneLevel: 0,
        microphoneStatus: '正常',
        systemAudioLevel: 18,
        systemAudioReady: true,
        systemAudioStatus: '传输异常',
        microphonePartial: '',
        systemAudioPartial: '旧的部分转写',
        asrReady: true,
        asrStatus: '已就绪',
        visualSettings: DEFAULT_VISUAL_SETTINGS,
        lastFrameBytes: null,
        lastFrameOverTarget: false,
        lastVisualBatchAt: null,
        visualPipelineStatus: 'local-preview'
      })
    )

    expect(markup).toContain('传输异常')
    expect(markup).not.toContain('旧的部分转写')
  })
})
