import { describe, expect, it } from 'vitest'
import { runtimeConfigMatchesLocal } from './useAudienceRuntimeControl'
import { getProviderProbeDisplay } from '../features/live/LiveStage'

describe('recovered runtime fingerprint', () => {
  it('marks the workspace applied only when the backend and local canonical hashes match', () => {
    const hash = 'a'.repeat(64)
    expect(runtimeConfigMatchesLocal(hash, hash)).toBe(true)
    expect(runtimeConfigMatchesLocal(hash, 'b'.repeat(64))).toBe(false)
    expect(runtimeConfigMatchesLocal('not-a-hash', 'not-a-hash')).toBe(false)
  })
})

describe('provider probe status display', () => {
  const baseState = {
    backendConnected: true,
    providerConfigured: true,
    probing: false,
    probe: null,
    error: null
  }

  it('distinguishes setup, progress, success, and failure states', () => {
    expect(getProviderProbeDisplay({ ...baseState, providerConfigured: false }).label).toBe('未配置')
    expect(getProviderProbeDisplay({ ...baseState, probing: true }).label).toBe('检测中')
    expect(getProviderProbeDisplay({
      ...baseState,
      probe: {
        provider_profile_id: 'default',
        status: 'passed',
        discovered_model_ids: [],
        checks: []
      }
    }).label).toBe('已通过')
    const failed = getProviderProbeDisplay({ ...baseState, error: 'Provider 能力探测超时。' })
    expect(failed.label).toBe('检测失败')
    expect(failed.detail).toBe('Provider 能力探测超时。')
  })
})
