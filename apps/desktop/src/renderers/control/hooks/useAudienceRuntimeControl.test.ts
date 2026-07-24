import { describe, expect, it } from 'vitest'
import { runtimeConfigMatchesLocal } from './useAudienceRuntimeControl'

describe('recovered runtime fingerprint', () => {
  it('marks the workspace applied only when the backend and local canonical hashes match', () => {
    const hash = 'a'.repeat(64)
    expect(runtimeConfigMatchesLocal(hash, hash)).toBe(true)
    expect(runtimeConfigMatchesLocal(hash, 'b'.repeat(64))).toBe(false)
    expect(runtimeConfigMatchesLocal('not-a-hash', 'not-a-hash')).toBe(false)
  })
})
