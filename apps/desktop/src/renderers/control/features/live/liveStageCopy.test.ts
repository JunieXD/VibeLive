import { describe, expect, it } from 'vitest'
import { getLiveMessagePlaceholder } from './LiveStage'

describe('getLiveMessagePlaceholder', () => {
  it('prompts a configured idle session to start streaming instead of configuring again', () => {
    expect(
      getLiveMessagePlaceholder({
        audienceSessionActive: false,
        sessionStatus: 'idle',
        providerConfigured: true
      })
    ).toBe('开始直播后可与 AI 观众互动')
  })

  it('uses the Chinese supplier name when configuration is still missing', () => {
    expect(
      getLiveMessagePlaceholder({
        audienceSessionActive: false,
        sessionStatus: 'idle',
        providerConfigured: false
      })
    ).toBe('配置供应商后可与 AI 观众互动')
  })

  it('shows the interactive prompt while the audience runtime is live', () => {
    expect(
      getLiveMessagePlaceholder({
        audienceSessionActive: true,
        sessionStatus: 'running',
        providerConfigured: true
      })
    ).toBe('说点什么，AI 观众会回应你')
  })
})
