import type {
  AudienceModeState,
  AudienceRuntimeSnapshot,
  MemeEntry,
  Persona,
  RuntimePersona
} from './types'

export function compileAudienceRuntimeSnapshot(
  personas: readonly Persona[],
  modeState: AudienceModeState,
  memes: readonly MemeEntry[]
): AudienceRuntimeSnapshot {
  const mode = modeState.modes.find((candidate) => candidate.id === modeState.activeModeId)
  if (!mode) throw new Error(`Active audience mode does not exist: ${modeState.activeModeId}`)

  const personasById = new Map(personas.map((persona) => [persona.id, persona]))
  const runtimePersonas: RuntimePersona[] = mode.personaIds.map((personaId) => {
    const base = personasById.get(personaId)
    if (!base) throw new Error(`Mode ${mode.id} references unknown persona: ${personaId}`)
    const override = mode.personaOverrides[personaId]
    return {
      ...base,
      ...override,
      traits: override?.traits ? [...override.traits] : [...base.traits],
      triggerPreferences: override?.triggerPreferences
        ? [...override.triggerPreferences]
        : [...base.triggerPreferences],
      avoidPatterns: override?.avoidPatterns ? [...override.avoidPatterns] : [...base.avoidPatterns],
      contentFlags: override?.contentFlags ? [...override.contentFlags] : [...base.contentFlags],
      basePersonaId: base.id,
      weight: mode.personaWeights[personaId]
    }
  }).filter((persona) => persona.enabled)

  return {
    mode: {
      id: mode.id,
      name: mode.name,
      description: mode.description,
      baseActivity: [...mode.baseActivity],
      burstLimit: [...mode.burstLimit],
      ambience: mode.ambience
    },
    personas: runtimePersonas,
    memes: memes
      .filter((meme) => meme.modeId === mode.id && meme.status === 'active')
      .sort((left, right) => Number(right.pinned) - Number(left.pinned))
  }
}
