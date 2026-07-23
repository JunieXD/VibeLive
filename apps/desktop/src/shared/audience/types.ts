export type Persona = {
  readonly id: string
  readonly name: string
  readonly initials: string
  readonly role: string
  readonly color: string
  readonly traits: readonly string[]
  readonly speechStyle: string
  readonly behavior: string
  readonly triggerPreferences: readonly string[]
  readonly avoidPatterns: readonly string[]
  readonly silenceBias: 0 | 1 | 2 | 3 | 4
  readonly burstBias: 0 | 1 | 2 | 3 | 4
  readonly repetitionBias: 0 | 1 | 2 | 3 | 4
  readonly cooldownMs: number
  readonly maxCommentsPerDecision: 1 | 2
  readonly contentFlags: readonly string[]
  readonly enabled: boolean
}

export type PersonaOverride = Partial<
  Pick<
    Persona,
    | 'name'
    | 'initials'
    | 'role'
    | 'color'
    | 'traits'
    | 'speechStyle'
    | 'behavior'
    | 'triggerPreferences'
    | 'avoidPatterns'
    | 'silenceBias'
    | 'burstBias'
    | 'repetitionBias'
    | 'cooldownMs'
    | 'maxCommentsPerDecision'
    | 'contentFlags'
    | 'enabled'
  >
>

export type AudienceAmbience = 'natural' | 'continuous'

export type AudienceMode = {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly builtIn: boolean
  readonly personaIds: readonly string[]
  readonly personaWeights: Readonly<Record<string, number>>
  readonly personaOverrides: Readonly<Record<string, PersonaOverride>>
  readonly baseActivity: readonly [minimum: number, maximum: number]
  readonly burstLimit: readonly [minimum: number, maximum: number]
  readonly ambience: AudienceAmbience
}

export type AudienceModeState = {
  readonly modes: readonly AudienceMode[]
  readonly activeModeId: string
}

export type MemeStatus = 'active' | 'inactive' | 'archived'
export type MemeSource = 'automatic' | 'manual'
export type MemeSourceKind =
  | 'user_text'
  | 'user_speech'
  | 'screen_event'
  | 'audience_barrage'
  | 'manual'
export type MemeCreator = 'director' | 'user'

export type CrowdDecision = {
  readonly modeId: string
  readonly audienceIds: readonly string[]
  readonly intent: string
  readonly observationIds: readonly string[]
  readonly createdAt: string
}

export type MemeCandidate = {
  readonly id: string
  readonly modeId: string
  readonly text: string
  readonly familyKey?: string
  readonly personaTags?: readonly string[]
  readonly sourceKinds: readonly MemeSourceKind[]
  readonly evidenceSummary: string
  readonly createdAt: string
}

export type MemeEntry = {
  readonly id: string
  readonly modeId: string
  readonly text: string
  readonly normalizedText: string
  readonly familyKey: string
  readonly personaTags: readonly string[]
  readonly sourceKinds: readonly MemeSourceKind[]
  readonly evidenceSummary: string
  readonly createdBy: MemeCreator
  readonly source: MemeSource
  readonly createdAt: string
  readonly revision: number
  readonly lastUsedAt: string | null
  readonly usageCount: number
  readonly status: MemeStatus
  readonly pinned: boolean
}

export type RuntimePersona = Persona & {
  readonly basePersonaId: string
  readonly weight: number
}

export type AudienceRuntimeSnapshot = {
  readonly mode: Pick<
    AudienceMode,
    'id' | 'name' | 'description' | 'baseActivity' | 'burstLimit' | 'ambience'
  >
  readonly personas: readonly RuntimePersona[]
  readonly memes: readonly MemeEntry[]
}

export type AudienceWorkspaceState = {
  readonly version: 1
  readonly personas: readonly Persona[]
  readonly modeState: AudienceModeState
  readonly memes: readonly MemeEntry[]
}
