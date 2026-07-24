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

export function allocateViewerCounts(
  mode: AudienceMode,
  personas: readonly PersonaTemplate[]
): readonly PersonaAllocation[] {
  const personasById = new Map(personas.map((persona) => [persona.id, persona]))
  const eligible = mode.personaIds.flatMap((personaId, modeIndex) => {
    const persona = personasById.get(personaId)
    const weight = mode.personaWeights[personaId]
    return persona?.enabled && Number.isFinite(weight) && weight > 0
      ? [{ personaId, modeIndex, weight }]
      : []
  })
  const totalWeight = eligible.reduce((total, item) => total + item.weight, 0)
  if (totalWeight <= 0) throw new Error(`Mode ${mode.id} has no enabled weighted personas`)

  const allocations = eligible.map((item) => {
    const exact = mode.viewerCount * item.weight / totalWeight
    return { ...item, count: Math.floor(exact), remainder: exact - Math.floor(exact) }
  })
  let remaining = mode.viewerCount - allocations.reduce((total, item) => total + item.count, 0)
  const remainderOrder = [...allocations].sort((left, right) =>
    right.remainder - left.remainder ||
    left.modeIndex - right.modeIndex ||
    compareStableId(left.personaId, right.personaId)
  )
  for (let index = 0; index < remaining; index += 1) remainderOrder[index].count += 1
  return allocations
    .filter((item) => item.count > 0)
    .map(({ personaId, count }) => ({ personaId, count }))
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
        weight: mode.personaWeights[personaId],
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

function compareStableId(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0
}
