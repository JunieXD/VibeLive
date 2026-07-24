import { describe, expect, it } from 'vitest'
import { canSaveModelConfig, getModelConfigNotice } from './useModelConfig'

describe('model configuration notices', () => {
  it('explains when an ASR change applies to the next session', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default',
        securelyStored: true,
        runtimeApplyRequired: false,
        nextSessionRequired: true
      })
    ).toContain('下一场直播加载')
  })

  it('reports that securely stored credentials load when a session starts or recovers', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default',
        securelyStored: true,
        runtimeApplyRequired: false,
        nextSessionRequired: false
      })
    ).toBe('模型与语音识别配置已安全保存；开始或恢复直播时将加载 Provider')
  })

  it('warns when credentials cannot be persisted securely', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default',
        securelyStored: false,
        runtimeApplyRequired: false,
        nextSessionRequired: false
      })
    ).toContain('密钥不会落盘')
  })

  it('allows saved credentials to satisfy both secret fields', () => {
    expect(
      canSaveModelConfig({
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4.1',
        apiKey: '',
        asrApiKey: '',
        status: {
          baseUrl: 'https://api.openai.com/v1',
          providerProfileId: 'default',
          model: 'gpt-4.1',
          directorModel: null,
          viewerModel: null,
          memoryModel: null,
          visualSummaryModel: null,
          modelApiKeyStored: true,
          asrApiKeyStored: true
        },
        backendConnection: 'connected',
        loading: false,
        saving: false
      })
    ).toBe(true)
  })

  it('blocks saving while the backend is unavailable', () => {
    expect(
      canSaveModelConfig({
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4.1',
        apiKey: 'model-key',
        asrApiKey: 'asr-key',
        status: null,
        backendConnection: 'failed',
        loading: false,
        saving: false
      })
    ).toBe(false)
  })

  it('describes active-session saves as pending an explicit runtime apply', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default-rev-12345678',
        securelyStored: true,
        runtimeApplyRequired: true,
        nextSessionRequired: false
      })
    ).toContain('显式应用运行时配置后切换')
  })

  it('separates model apply from ASR restart guidance', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default-rev-12345678',
        securelyStored: true,
        runtimeApplyRequired: true,
        nextSessionRequired: true
      })
    ).toBe('模型配置已安全保存，显式应用运行时配置后切换；ASR 配置将在下一场直播加载')
  })
})
