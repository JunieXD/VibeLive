import { describe, expect, it } from 'vitest'
import { shouldAcceptBackendBarrage } from './useAudienceBarrage'

describe('backend barrage delivery gate', () => {
  it('does not backfill late barrages while paused or after provider failure', () => {
    expect(shouldAcceptBackendBarrage('running')).toBe(true)
    expect(shouldAcceptBackendBarrage('paused')).toBe(false)
    expect(shouldAcceptBackendBarrage('error')).toBe(false)
    expect(shouldAcceptBackendBarrage('stopping')).toBe(false)
    expect(shouldAcceptBackendBarrage('running', 999, 1_000)).toBe(false)
    expect(shouldAcceptBackendBarrage('running', 1_000, 1_000)).toBe(true)
  })
})
