import { BUILT_IN_MODES } from './presets'
import type { AudienceMode, AudienceModeState, PersonaOverride } from './types'

export function createInitialModeState(): AudienceModeState {
  return { modes: BUILT_IN_MODES, activeModeId: BUILT_IN_MODES[0].id }
}

export function activateMode(state: AudienceModeState, modeId: string): AudienceModeState {
  if (!state.modes.some((mode) => mode.id === modeId)) {
    throw new Error(`Unknown audience mode: ${modeId}`)
  }
  return state.activeModeId === modeId ? state : { ...state, activeModeId: modeId }
}

export function resetBuiltInMode(
  state: AudienceModeState,
  modeId: string,
  builtIns: readonly AudienceMode[] = BUILT_IN_MODES
): AudienceModeState {
  const original = builtIns.find((mode) => mode.id === modeId)
  if (!original?.builtIn) throw new Error(`Unknown built-in audience mode: ${modeId}`)
  if (!state.modes.some((mode) => mode.id === modeId)) {
    return { ...state, modes: [...state.modes, original] }
  }
  return {
    ...state,
    modes: state.modes.map((mode) => (mode.id === modeId ? original : mode))
  }
}

export function duplicateModeAsCustom(
  state: AudienceModeState,
  sourceModeId: string,
  customId: string,
  customName: string
): AudienceModeState {
  const source = state.modes.find((mode) => mode.id === sourceModeId)
  if (!source) throw new Error(`Unknown audience mode: ${sourceModeId}`)
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(customId)) throw new Error('Custom mode id must be kebab-case')
  if (state.modes.some((mode) => mode.id === customId)) throw new Error(`Audience mode already exists: ${customId}`)
  if (!customName.trim()) throw new Error('Custom mode name is required')

  const copy: AudienceMode = {
    ...source,
    id: customId,
    namespaceId: customId,
    revision: 1,
    name: customName.trim(),
    builtIn: false,
    personaIds: [...source.personaIds],
    personaWeights: { ...source.personaWeights },
    personaOverrides: Object.fromEntries(
      Object.entries(source.personaOverrides).map(([id, override]) => [
        id,
        clonePersonaOverride(override)
      ])
    )
  }
  return { modes: [...state.modes, copy], activeModeId: copy.id }
}

export function reviseAudienceMode(
  current: AudienceMode,
  patch: Partial<Omit<AudienceMode, 'id' | 'builtIn' | 'revision' | 'baseActivity' | 'burstLimit'>>
): AudienceMode {
  const candidate = normalizeModeAliases({ ...current, ...patch })
  return canonicalModeContent(candidate) === canonicalModeContent(current)
    ? current
    : { ...candidate, revision: current.revision + 1 }
}

export function canonicalModeContent(mode: AudienceMode): string {
  return `${JSON.stringify({
    mode_id: mode.id,
    namespace_id: mode.namespaceId,
    name: mode.name,
    description: mode.description,
    built_in: mode.builtIn,
    target_concurrent_viewers: mode.targetConcurrentViewers,
    persona_ids: [...mode.personaIds],
    persona_weights: Object.fromEntries(
      mode.personaIds.map((personaId) => [personaId, mode.personaWeights[personaId]])
    ),
    persona_overrides: Object.fromEntries(
      Object.keys(mode.personaOverrides).sort().map((personaId) => [
        personaId,
        canonicalOverride(mode.personaOverrides[personaId])
      ])
    ),
    normal_response_range: [...mode.normalResponseRange],
    highlight_response_range: [...mode.highlightResponseRange],
    ambience: mode.ambience,
    visual_settings: {
      barrage_generation_mode: mode.visualSettings.barrageGenerationMode,
      viewer_visual_input_mode: mode.visualSettings.viewerVisualInputMode,
      frame_bundle_size: mode.visualSettings.frameBundleSize,
      frame_window_ms: mode.visualSettings.frameWindowMs,
      frame_selection_strategy: mode.visualSettings.frameSelectionStrategy,
      frame_max_dimension: mode.visualSettings.frameMaxDimension,
      frame_quality: mode.visualSettings.frameQuality
    }
  })}\n`
}

function canonicalOverride(override: PersonaOverride): PersonaOverride {
  const entries = [
    ['name', override.name],
    ['initials', override.initials],
    ['role', override.role],
    ['color', override.color],
    ['traits', override.traits ? [...override.traits] : undefined],
    ['speechStyle', override.speechStyle],
    ['behavior', override.behavior],
    ['triggerPreferences', override.triggerPreferences
      ? [...override.triggerPreferences]
      : undefined],
    ['avoidPatterns', override.avoidPatterns ? [...override.avoidPatterns] : undefined],
    ['silenceBias', override.silenceBias],
    ['burstBias', override.burstBias],
    ['repetitionBias', override.repetitionBias],
    ['cooldownMs', override.cooldownMs],
    ['maxCommentsPerDecision', override.maxCommentsPerDecision],
    ['contentFlags', override.contentFlags ? [...override.contentFlags] : undefined],
    ['enabled', override.enabled]
  ] as const
  return Object.fromEntries(entries.filter(([, value]) => value !== undefined)) as PersonaOverride
}

function normalizeModeAliases(mode: AudienceMode): AudienceMode {
  return {
    ...mode,
    personaIds: [...mode.personaIds],
    personaWeights: { ...mode.personaWeights },
    personaOverrides: { ...mode.personaOverrides },
    normalResponseRange: [...mode.normalResponseRange],
    highlightResponseRange: [...mode.highlightResponseRange],
    visualSettings: { ...mode.visualSettings },
    baseActivity: [...mode.normalResponseRange],
    burstLimit: [...mode.highlightResponseRange]
  }
}

function clonePersonaOverride(override: PersonaOverride): PersonaOverride {
  const clone = { ...override }
  for (const field of ['traits', 'triggerPreferences', 'avoidPatterns', 'contentFlags'] as const) {
    if (clone[field]) clone[field] = [...clone[field]]
  }
  return clone
}
