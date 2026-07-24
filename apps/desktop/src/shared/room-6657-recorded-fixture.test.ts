import { readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import {
  activateMode,
  createInitialAudienceWorkspace,
  reviseAudienceMode
} from './audience'
import { compileCanonicalRuntimeSpec } from './backend-client'

const fixtureUrl = new URL(
  '../../../../tests/fixtures/cs2/viewer_runtime_recorded.json',
  import.meta.url
)
const primaryPersonaIds = [
  'reaction_qmark',
  'hardmouth_antifan',
  'instigator',
  'fun_seeker',
  'meme_archivist',
  'abstract_radio',
  'parrot_unit',
  'jinx_machine',
  'grudge_keeper'
] as const
const secondaryPersonaIds = [
  'cheat_suspector',
  'praise_then_bite',
  'clip_alarm',
  'room_historian'
] as const
const provider = {
  providerProfileId: 'synthetic-no-credentials',
  directorModel: 'fake-director-v1',
  viewerModel: 'fake-viewer-v1',
  memoryModel: 'fake-memory-v1',
  visualSummaryModel: 'fake-visual-v1'
}

function compileFixtureSpecs() {
  const initialWorkspace = createInitialAudienceWorkspace()
  const activeModeState = activateMode(initialWorkspace.modeState, 'room-6657')
  const activeWorkspace = { ...initialWorkspace, modeState: activeModeState }
  const updatedModeState = {
    ...activeModeState,
    modes: activeModeState.modes.map((mode) =>
      mode.id === 'room-6657'
        ? reviseAudienceMode(mode, {
            personaWeights: { ...mode.personaWeights, instigator: 8 }
          })
        : mode
    )
  }
  const initial = compileCanonicalRuntimeSpec(activeWorkspace, {
    configRevision: 1,
    provider,
    roomId: 'cs2-synthetic-room',
    roomDisplayName: 'CS2 Synthetic Room'
  })
  const updated = compileCanonicalRuntimeSpec(
    { ...activeWorkspace, modeState: updatedModeState },
    {
      configRevision: 2,
      provider,
      roomId: 'cs2-synthetic-room',
      roomDisplayName: 'CS2 Synthetic Room'
    }
  )
  const initialMode = initial.spec.modes.find((mode) => mode.mode_id === 'room-6657')
  const personaNames = Object.fromEntries(
    activeWorkspace.personas
      .filter((persona) => initialMode?.persona_ids.includes(persona.id))
      .map((persona) => [persona.id, persona.name])
  )
  if (!initialMode) throw new Error('room-6657 is missing from the Desktop workspace')
  return {
    initial,
    updated,
    desktopSource: {
      workspace_factory: 'apps/desktop/src/shared/audience/workspace.ts#createInitialAudienceWorkspace',
      preset_definition: 'apps/desktop/src/shared/audience/presets.ts#BUILT_IN_MODES[room-6657]',
      compiler: 'apps/desktop/src/shared/backend-client.ts#compileCanonicalRuntimeSpec',
      active_mode_id: 'room-6657',
      primary_persona_ids: primaryPersonaIds,
      secondary_persona_ids: secondaryPersonaIds,
      persona_alias_bases: personaNames,
      initial_viewer_count: initialMode.viewer_count,
      initial_persona_weights: initialMode.persona_weights
    }
  }
}

describe('recorded room-6657 fixture', () => {
  it('matches the canonical spec compiled from the real Desktop preset', () => {
    const fixture = JSON.parse(readFileSync(fixtureUrl, 'utf8'))
    const { initial, updated, desktopSource } = compileFixtureSpecs()

    if (process.env.ADVX_UPDATE_FIXTURES === '1') {
      fixture.fixture_version = 2
      fixture.desktop_source = desktopSource
      fixture.initial_canonical_runtime_spec = initial.spec
      fixture.hot_update = { persona_weight_updates: { instigator: 8 } }
      fixture.bundle.bundle_id = 'cs2-6657-desktop-preset-recorded-v2'
      fixture.bundle.canonical_runtime_spec = updated.spec
      fixture.bundle.config_hash = updated.configHash
      writeFileSync(fixtureUrl, `${JSON.stringify(fixture, null, 2)}\n`, 'utf8')
    }

    expect(fixture.desktop_source).toEqual(desktopSource)
    expect(fixture.initial_canonical_runtime_spec).toEqual(initial.spec)
    expect(fixture.bundle.canonical_runtime_spec).toEqual(updated.spec)
    expect(fixture.bundle.config_hash).toBe(updated.configHash)
    expect(createHash('sha256').update(updated.canonicalJson).digest('hex'))
      .toBe(fixture.bundle.config_hash)
  })
})
