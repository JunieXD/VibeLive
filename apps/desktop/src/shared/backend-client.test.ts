import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import type { CanonicalRuntimeSpec as ContractCanonicalRuntimeSpec } from '@advx/contracts'
import { describe, expect, it } from 'vitest'
import { createInitialAudienceWorkspace } from './audience'
import { canonicalJsonStringify, compileCanonicalRuntimeSpec } from './backend-client'

describe('canonical desktop runtime spec', () => {
  it('compiles the full v3 snapshot with backend-compatible hashes and frame quality', () => {
    const workspace = createInitialAudienceWorkspace()
    const compiled = compileCanonicalRuntimeSpec(workspace, {
      configRevision: 3,
      provider: {
        providerProfileId: 'profile-a',
        viewerModel: 'viewer-model',
        memoryModel: 'memory-model',
        visualSummaryModel: 'summary-model'
      }
    })
    expect(compiled.spec).toMatchObject({
      protocol_version: 3,
      audience_contract_version: 3,
      config_revision: 3,
      active_mode_id: 'lively-game-room',
      provider: {
        provider_profile_id: 'profile-a',
        viewer_model: 'viewer-model',
        memory_model: 'memory-model',
        visual_summary_model: 'summary-model'
      },
      settings: {
        barrage_generation_mode: 'per_viewer',
        window_batch_interval_ms: 5_000,
        window_batch_context_window_ms: 30_000,
        window_batch_max_frames: 5,
        frame_bundle: {
          frame_bundle_size: 15,
          frame_selection_strategy: 'change_peaks',
          frame_quality: 82
        },
        viewer_visual_input_mode: 'direct_frames',
        max_in_flight_viewer_requests: 6,
        viewer_request_ttl_ms: 30_000,
        viewer_queue_capacity: 64,
        observation_merge_window_ms: 1_000,
        public_context_window_ms: 60_000,
        public_context_max_events: 48,
        replyable_event_window_ms: 30_000,
        max_replyable_events: 8,
        viewer_user_speaker_budget: 6,
        viewer_screen_speaker_budget: 4,
        viewer_ambient_speaker_budget: 2,
        max_direct_frame_age_ms: 30_000,
        screen_change_threshold: 0.2,
        screen_change_cooldown_ms: 10_000
      }
    })
    expect(compiled.spec.personas[0].content_hash).toMatch(/^[0-9a-f]{64}$/)
    expect(compiled.spec.personas[0].content_hash).not.toContain('sha256:')
    expect(compiled.spec.modes.map((mode) =>
      Object.values(mode.persona_counts).reduce((total, count) => total + count, 0)
    )).toEqual([24, 28, 16, 14, 24, 14])
    expect(compiled.configHash).toBe(
      createHash('sha256').update(compiled.canonicalJson).digest('hex')
    )
    const generatedContract: ContractCanonicalRuntimeSpec = compiled.spec
    expect(generatedContract.settings?.frame_bundle?.frame_quality).toBe(82)
  })

  it('compiles window generation with a five-frame thirty-second ceiling', () => {
    const workspace = createInitialAudienceWorkspace()
    const activeMode = workspace.modeState.modes[0]
    const windowWorkspace = {
      ...workspace,
      modeState: {
        ...workspace.modeState,
        modes: workspace.modeState.modes.map((mode) =>
          mode.id === activeMode.id
            ? {
                ...mode,
                visualSettings: {
                  ...mode.visualSettings,
                  barrageGenerationMode: 'window_batch' as const,
                  viewerVisualInputMode: 'shared_summary' as const,
                  frameBundleSize: 2,
                  frameWindowMs: 10_000,
                  frameSelectionStrategy: 'latest_n' as const
                }
              }
            : mode
        )
      }
    }

    const compiled = compileCanonicalRuntimeSpec(windowWorkspace, {
      configRevision: 4,
      provider: {
        providerProfileId: 'profile-a',
        viewerModel: 'viewer-model',
        memoryModel: 'memory-model',
        visualSummaryModel: 'summary-model'
      }
    })

    expect(compiled.spec.settings).toMatchObject({
      barrage_generation_mode: 'window_batch',
      window_batch_interval_ms: 5_000,
      window_batch_context_window_ms: 30_000,
      window_batch_max_frames: 5,
      viewer_visual_input_mode: 'direct_frames',
      public_context_window_ms: 30_000,
      frame_bundle: {
        frame_bundle_size: 5,
        frame_window_ms: 30_000,
        frame_selection_strategy: 'change_peaks'
      }
    })
  })

  it('sorts object keys recursively without reordering arrays', () => {
    expect(canonicalJsonStringify({ z: { b: 1, a: 2 }, a: [3, { y: 4, x: 5 }] }))
      .toBe('{"a":[3,{"x":5,"y":4}],"z":{"a":2,"b":1}}')
    expect(canonicalJsonStringify({
      '\ue000': 'bmp-private-use',
      '\u{1f600}': 'astral',
      2: 'two',
      10: 'ten'
    })).toBe('{"10":"ten","2":"two","\u{1f600}":"astral","\ue000":"bmp-private-use"}')
  })

  it('matches the backend hash for zero, one, and fractional runtime biases', () => {
    const fixture = JSON.parse(
      readFileSync(
        new URL('../../../../tests/fixtures/cs2/canonical_runtime_numeric_parity.json', import.meta.url),
        'utf8'
      )
    ) as {
      expected_config_hash: string
      spec: ContractCanonicalRuntimeSpec
    }

    const canonicalJson = canonicalJsonStringify(fixture.spec)

    expect(canonicalJson).toContain('"silence_bias":0')
    expect(canonicalJson).toContain('"burst_bias":1')
    expect(canonicalJson).toContain('"repetition_bias":0.125')
    expect(canonicalJson).toContain(
      '"ordering_probe":{"10":"ten","2":"two","\u{1f600}":"astral",' +
      '"\ue000":"bmp-private-use"}'
    )
    expect(createHash('sha256').update(canonicalJson).digest('hex'))
      .toBe(fixture.expected_config_hash)
  })

  it('normalizes signed zero and rejects non-finite canonical numbers', () => {
    expect(canonicalJsonStringify({ decimal: 0.125, negativeZero: -0, one: 1.0, zero: 0.0 }))
      .toBe('{"decimal":0.125,"negativeZero":0,"one":1,"zero":0}')
    expect(() => canonicalJsonStringify({ invalid: Number.NaN }))
      .toThrow('canonical JSON numbers must be finite')
  })
})
