import type {
  AudienceMode,
  PersonaTemplate,
  RuntimePersona,
  ViewerVariant
} from './types'

export type PersonaAllocation = {
  readonly personaId: string
  readonly count: number
}

export function totalViewerCount(mode: AudienceMode): number {
  return Object.values(mode.personaCounts).reduce((total, count) => total + count, 0)
}

export function allocateViewerCounts(
  mode: AudienceMode,
  personas: readonly PersonaTemplate[]
): readonly PersonaAllocation[] {
  const personasById = new Map(personas.map((persona) => [persona.id, persona]))
  const allocations = Object.entries(mode.personaCounts).flatMap(([personaId, count]) => {
    const persona = personasById.get(personaId)
    if (!Number.isInteger(count) || count < 0) {
      throw new Error(`Mode ${mode.id} has an invalid viewer count for ${personaId}`)
    }
    if (count === 0) return []
    if (!persona?.enabled) {
      throw new Error(`Mode ${mode.id} assigns viewers to an unavailable persona`)
    }
    return [{ personaId, count }]
  })
  if (allocations.length === 0) throw new Error(`Mode ${mode.id} has no assigned viewers`)
  return allocations
}

export function compileViewerPool(
  mode: AudienceMode,
  personas: readonly PersonaTemplate[],
  sessionSeed = 'default'
): readonly RuntimePersona[] {
  const personasById = new Map(personas.map((persona) => [persona.id, persona]))
  const sessionKey = stableHash(sessionSeed).toString(16).padStart(8, '0')
  return allocateViewerCounts(mode, personas).flatMap(({ personaId, count }) => {
    const persona = personasById.get(personaId)
    if (!persona) return []
    const override = mode.personaOverrides[personaId]
    return Array.from({ length: count }, (_, index) => {
      const ordinal = index + 1
      const viewerInstanceId =
        `viewer:${sessionKey}:${mode.id}:${personaId}:${String(ordinal).padStart(2, '0')}`
      const name = override?.name ?? persona.name
      return {
        ...persona,
        ...override,
        id: viewerInstanceId,
        traits: override?.traits ? [...override.traits] : [...persona.traits],
        triggerPreferences: override?.triggerPreferences
          ? [...override.triggerPreferences]
          : [...persona.triggerPreferences],
        avoidPatterns: override?.avoidPatterns
          ? [...override.avoidPatterns]
          : [...persona.avoidPatterns],
        contentFlags: override?.contentFlags ? [...override.contentFlags] : [...persona.contentFlags],
        viewerInstanceId,
        basePersonaId: persona.id,
        personaRevision: persona.revision,
        personaContentHash: persona.contentHash,
        ordinal,
        alias: `${name}·${String(ordinal).padStart(2, '0')}`,
        variant: deriveViewerVariant(sessionSeed, personaId, ordinal)
      }
    })
  })
}

export function deriveViewerVariant(
  sessionSeed: string,
  personaId: string,
  ordinal: number
): ViewerVariant {
  const seed = stableHash(`${sessionSeed}\u0000${personaId}\u0000${ordinal}`)
  const options = <T>(values: readonly T[], offset: number): T =>
    values[(seed >>> offset) % values.length]
  return {
    expressionLength: options(['short', 'balanced', 'expanded'] as const, 0),
    stanceIntensity: options([0, 1, 2] as const, 3),
    memeAffinity: options([0, 1, 2] as const, 6),
    attentionFocus: options(['action', 'conversation', 'context'] as const, 9),
    silenceTendency: options([0, 1, 2] as const, 12)
  }
}

function stableHash(value: string): number {
  let hash = 2166136261
  for (const character of value) {
    hash ^= character.charCodeAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}
