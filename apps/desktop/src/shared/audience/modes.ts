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

function clonePersonaOverride(override: PersonaOverride): PersonaOverride {
  const clone = { ...override }
  for (const field of ['traits', 'triggerPreferences', 'avoidPatterns', 'contentFlags'] as const) {
    if (clone[field]) clone[field] = [...clone[field]]
  }
  return clone
}
