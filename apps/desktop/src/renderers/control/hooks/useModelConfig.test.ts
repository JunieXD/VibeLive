import { describe, expect, it } from 'vitest'
import { canSaveModelConfig, getModelConfigNotice } from './useModelConfig'

describe('model configuration notices', () => {
  it('prioritizes restart-required guidance', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default',
        securelyStored: true,
        backendConfigured: false,
        restartRequired: true,
        runtimeApplyRequired: false
      })
    ).toContain('重启后加载')
  })

  it('reports secure storage for both model and speech credentials', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default',
        securelyStored: true,
        backendConfigured: true,
        restartRequired: false,
        runtimeApplyRequired: false
      })
    ).toBe('模型与语音识别配置已安全保存并接入后端')
  })

  it('warns when credentials cannot be persisted securely', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default',
        securelyStored: false,
        backendConfigured: true,
        restartRequired: false,
        runtimeApplyRequired: false
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
        backendConfigured: false,
        restartRequired: false,
        runtimeApplyRequired: true
      })
    ).toContain('显式应用运行时配置后切换')
  })

  it('separates model apply from ASR restart guidance', () => {
    expect(
      getModelConfigNotice({
        ok: true,
        providerProfileId: 'default-rev-12345678',
        securelyStored: true,
        backendConfigured: false,
        restartRequired: true,
        runtimeApplyRequired: true
      })
    ).toBe('模型配置已安全保存，显式应用运行时配置后切换；ASR 配置需重启后加载')
  })
})
