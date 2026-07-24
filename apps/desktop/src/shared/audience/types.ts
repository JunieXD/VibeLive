export const AUDIENCE_WORKSPACE_VERSION = 3 as const
export const PERSONA_DOCUMENT_VERSION = 2 as const

export type PersonaTemplate = {
  readonly documentVersion: typeof PERSONA_DOCUMENT_VERSION
  readonly revision: number
  readonly contentHash: string
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

export type PersonaContent = Omit<
  PersonaTemplate,
  'documentVersion' | 'revision' | 'contentHash'
>

// Kept as the renderer-facing content shape while renderer migration is intentionally deferred.
export type Persona = PersonaTemplate | PersonaContent

export type PersonaOverride = Partial<
  Pick<
    PersonaContent,
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
export type ViewerVisualInputMode = 'direct_frames' | 'shared_summary'
export type FrameSelectionStrategy = 'latest_n' | 'evenly_spaced' | 'change_peaks'

export type AudienceVisualSettings = {
  readonly viewerVisualInputMode: ViewerVisualInputMode
  readonly frameBundleSize: number
  readonly frameWindowMs: number
  readonly frameSelectionStrategy: FrameSelectionStrategy
  readonly frameMaxDimension: number
  readonly frameQuality: number
}

export type AudienceMode = {
  readonly id: string
  readonly namespaceId: string
  readonly revision: number
  readonly name: string
  readonly description: string
  readonly builtIn: boolean
  readonly targetConcurrentViewers: number
  readonly personaIds: readonly string[]
  readonly personaWeights: Readonly<Record<string, number>>
  readonly personaOverrides: Readonly<Record<string, PersonaOverride>>
  readonly normalResponseRange: readonly [minimum: number, maximum: number]
  readonly highlightResponseRange: readonly [minimum: number, maximum: number]
  readonly ambience: AudienceAmbience
  readonly visualSettings: AudienceVisualSettings
  /** @deprecated Renderer-only compatibility alias for normalResponseRange. */
  readonly baseActivity: readonly [minimum: number, maximum: number]
  /** @deprecated Renderer-only compatibility alias for highlightResponseRange. */
  readonly burstLimit: readonly [minimum: number, maximum: number]
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

export type ViewerVariant = {
  readonly expressionLength: 'short' | 'balanced' | 'expanded'
  readonly stanceIntensity: 0 | 1 | 2
  readonly memeAffinity: 0 | 1 | 2
  readonly attentionFocus: 'action' | 'conversation' | 'context'
  readonly silenceTendency: 0 | 1 | 2
}

export type RuntimePersona = PersonaContent & {
  readonly id: string
  readonly viewerInstanceId: string
  readonly basePersonaId: string
  readonly personaRevision: number
  readonly personaContentHash: string
  readonly ordinal: number
  readonly alias: string
  readonly weight: number
  readonly variant: ViewerVariant
}

export type AudienceRuntimeSnapshot = {
  readonly mode: Pick<
    AudienceMode,
    | 'id'
    | 'namespaceId'
    | 'revision'
    | 'name'
    | 'description'
    | 'targetConcurrentViewers'
    | 'normalResponseRange'
    | 'highlightResponseRange'
    | 'ambience'
    | 'visualSettings'
    | 'baseActivity'
    | 'burstLimit'
  >
  readonly personas: readonly RuntimePersona[]
}

export type AudienceWorkspaceState = {
  readonly version: typeof AUDIENCE_WORKSPACE_VERSION
  readonly personas: readonly Persona[]
  readonly modeState: AudienceModeState
}
