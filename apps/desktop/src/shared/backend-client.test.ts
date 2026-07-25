import { describe, expect, it } from 'vitest'
import { createInitialAudienceWorkspace } from './audience'
import { compileCanonicalRuntimeSpec } from './backend-client'

describe('compileCanonicalRuntimeSpec', () => {
  it('uses the product screen-change threshold', () => {
    const compiled = compileCanonicalRuntimeSpec(createInitialAudienceWorkspace(), {
      configRevision: 1,
      provider: {
        providerProfileId: 'default',
        viewerModel: 'viewer-model',
        memoryModel: 'memory-model',
        visualSummaryModel: 'visual-model'
      }
    })

    expect(compiled.spec.settings.screen_change_threshold).toBe(0.1)
  })
})
