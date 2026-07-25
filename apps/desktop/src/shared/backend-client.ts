import { createHash } from 'node:crypto'
import type {
  CanonicalRuntimeSpec as ContractCanonicalRuntimeSpec,
  ModeDefinition,
  PersonaTemplate as ContractPersonaTemplate,
  ProviderCapabilityProbeResult,
  RuntimeSessionSnapshot,
  RuntimeSettings,
  TraceQueryResponse,
  ViewerRequestTrace
} from '@advx/contracts'
import {
  createPersonaTemplate,
  totalViewerCount,
  type AudienceMode,
  type AudienceWorkspaceState,
  type PersonaOverride,
  type PersonaTemplate
} from './audience'

type Defaulted<T, K extends keyof T> = Omit<T, K> & Required<Pick<T, K>>

const FRAME_SIMILARITY_THRESHOLD = 0.95

export type CanonicalPersonaTemplate = ContractPersonaTemplate
export type CanonicalModeDefinition = ModeDefinition
type CompiledRuntimeSettings = Defaulted<RuntimeSettings, 'frame_bundle'> & {
  barrage_generation_mode: AudienceMode['visualSettings']['barrageGenerationMode']
  screen_change_threshold: number
  screen_change_cooldown_ms: number
  public_context_window_ms: number
  public_context_max_events: number
  replyable_event_window_ms: number
  max_replyable_events: number
  viewer_user_speaker_budget: number
  viewer_screen_speaker_budget: number
  viewer_ambient_speaker_budget: number
  max_direct_frame_age_ms: number
}
export type CanonicalRuntimeSpec = Omit<ContractCanonicalRuntimeSpec, 'settings'> & {
  settings: CompiledRuntimeSettings
}

export type CompiledRuntimeSpec = {
  spec: CanonicalRuntimeSpec
  canonicalJson: string
  configHash: string
}

export type RuntimeProviderModels = {
  providerProfileId: string
  viewerModel: string
  memoryModel: string
  visualSummaryModel: string
}

export type RuntimeCompileOptions = {
  configRevision: number
  provider: RuntimeProviderModels
  roomId?: string
  roomDisplayName?: string
  roomRevision?: number
}

export type RuntimeQuerySnapshot = Omit<
  RuntimeSessionSnapshot,
  'canonical_runtime_spec' | 'viewers'
> & {
  canonical_runtime_spec: CanonicalRuntimeSpec
  viewers: NonNullable<RuntimeSessionSnapshot['viewers']>
}

export type RuntimeApplySnapshot = RuntimeQuerySnapshot
export type RuntimeViewer = NonNullable<RuntimeSessionSnapshot['viewers']>[number]

export type ProviderProbeResult = ProviderCapabilityProbeResult
export type DebugTraceSummary = ViewerRequestTrace
export type DebugTraceQueryResult = TraceQueryResponse
export type AiCallTrace = import('@advx/contracts').components['schemas']['AiCallTrace']
export type AiCallImagePreview =
  import('@advx/contracts').components['schemas']['AiCallImagePreview']
export type AiCallQueryResponse =
  import('@advx/contracts').components['schemas']['AiCallQueryResponse']
export type AiCallRole = import('@advx/contracts').components['schemas']['AiCallRole']
export type AiCallStatus = import('@advx/contracts').components['schemas']['AiCallStatus']
export type AiCallQuery = {
  sessionId?: string
  role?: AiCallRole
  status?: AiCallStatus
  correlationId?: string
  cursor?: string
  limit?: number
}
export type RoomLongTermMemory = import('@advx/contracts').components['schemas']['RoomLongTermMemory']
export type RoomMemoryType = import('@advx/contracts').components['schemas']['RoomMemoryType']
export type MemoryCandidateRequest = import('@advx/contracts').components['schemas']['MemoryCandidateRequest']
export type MemoryResetResponse = import('@advx/contracts').components['schemas']['MemoryResetResponse']
export type ModeMeme = import('@advx/contracts').components['schemas']['ModeMeme']
export type MemeCandidate = import('@advx/contracts').components['schemas']['MemeCandidate']
export type CandidateCommitResponse =
  import('@advx/contracts').components['schemas']['CandidateCommitResponse']
export type AutoIngestResponse = import('@advx/contracts').components['schemas']['AutoIngestResponse']

export type LegacyMemeImportRequest =
  import('@advx/contracts').components['schemas']['LegacyMemeImportRequest']
export type LegacyMemeImportResponse =
  import('@advx/contracts').components['schemas']['LegacyMemeImportResponse']

export type TextSubmitTarget = {
  targetViewerId?: string
  targetPersonaId?: string
}

export type ModeMemeEdit = {
  text: string
  expectedRevision: number
  intensity?: number
}

export type RoomMemoryEdit = {
  content: string
  expectedRevision: number
  confidence?: number
  evidenceEventIds?: readonly string[]
}

export type RoomMemoryHead =
  import('@advx/contracts').components['schemas']['MemoryHeadResponse']

export function compileCanonicalRuntimeSpec(
  workspace: AudienceWorkspaceState,
  options: RuntimeCompileOptions
): CompiledRuntimeSpec {
  if (!Number.isInteger(options.configRevision) || options.configRevision < 1) {
    throw new Error('configRevision must be a positive integer')
  }
  const provider = normalizeRuntimeProvider(options.provider)
  const activeMode = workspace.modeState.modes.find(
    (mode) => mode.id === workspace.modeState.activeModeId
  )
  if (!activeMode) throw new Error('The active audience mode is missing')
  const personas = workspace.personas.map((persona) =>
    'documentVersion' in persona ? persona : createPersonaTemplate(persona)
  )
  const visual = activeMode.visualSettings
  const windowBatch = visual.barrageGenerationMode === 'window_batch'
  const spec: CanonicalRuntimeSpec = {
    protocol_version: 3,
    audience_contract_version: 3,
    config_revision: options.configRevision,
    room: {
      room_id: options.roomId ?? 'default-room',
      display_name: options.roomDisplayName ?? '默认直播间',
      revision: options.roomRevision ?? 1,
      created_at_ms: 0,
      updated_at_ms: 0
    },
    active_mode_id: activeMode.id,
    personas: personas.map(compilePersona),
    modes: workspace.modeState.modes.map(compileMode),
    provider: {
      provider_profile_id: provider.providerProfileId,
      viewer_model: provider.viewerModel,
      memory_model: provider.memoryModel,
      visual_summary_model: provider.visualSummaryModel
    },
    settings: {
      frame_bundle: {
        frame_bundle_size: windowBatch ? 5 : visual.frameBundleSize,
        frame_window_ms: windowBatch ? 30_000 : visual.frameWindowMs,
        frame_selection_strategy: windowBatch
          ? 'change_peaks'
          : visual.frameSelectionStrategy,
        frame_max_dimension: visual.frameMaxDimension,
        frame_quality: Math.max(1, Math.min(100, Math.round(visual.frameQuality * 100))),
        frame_similarity_threshold: FRAME_SIMILARITY_THRESHOLD,
        frame_anchor_interval_ms: 5_000
      },
      barrage_generation_mode: visual.barrageGenerationMode,
      window_batch_interval_ms: 5_000,
      window_batch_context_window_ms: 30_000,
      window_batch_max_frames: 5,
      viewer_visual_input_mode: windowBatch ? 'direct_frames' : visual.viewerVisualInputMode,
      max_in_flight_viewer_requests: Math.min(6, totalViewerCount(activeMode)),
      viewer_request_ttl_ms: 0,
      viewer_queue_capacity: 64,
      observation_merge_window_ms: 1_000,
      public_context_window_ms: windowBatch ? 30_000 : 60_000,
      public_context_max_events: 48,
      replyable_event_window_ms: 30_000,
      max_replyable_events: 8,
      viewer_user_speaker_budget: 6,
      viewer_screen_speaker_budget: 4,
      viewer_ambient_speaker_budget: 2,
      max_direct_frame_age_ms: 30_000,
      screen_change_threshold: 0.2,
      screen_change_cooldown_ms: 5_000,
      ambient_tick_cooldown_ms: 30_000,
      max_consecutive_ambient_waves: 1
    }
  }
  const canonicalJson = canonicalJsonStringify(spec)
  return {
    spec,
    canonicalJson,
    configHash: createHash('sha256').update(canonicalJson).digest('hex')
  }
}

function normalizeRuntimeProvider(provider: RuntimeProviderModels): RuntimeProviderModels {
  const normalized = {
    providerProfileId: provider.providerProfileId.trim() || 'default',
    viewerModel: provider.viewerModel.trim(),
    memoryModel: provider.memoryModel.trim(),
    visualSummaryModel: provider.visualSummaryModel.trim()
  }
  if (
    !normalized.viewerModel ||
    !normalized.memoryModel ||
    !normalized.visualSummaryModel
  ) {
    throw new Error('Configured models are required for all provider roles')
  }
  return normalized
}

export function canonicalJsonStringify(value: unknown): string {
  return serializeCanonical(value)
}

function compilePersona(persona: PersonaTemplate): CanonicalPersonaTemplate {
  return {
    persona_id: persona.id,
    document_version: persona.documentVersion,
    revision: persona.revision,
    content_hash: persona.contentHash.replace(/^sha256:/, ''),
    display_name: persona.name,
    role: persona.role,
    traits: [...persona.traits],
    speech_style: {
      instruction: persona.speechStyle,
      initials: persona.initials,
      color: persona.color,
      max_comments_per_decision: persona.maxCommentsPerDecision
    },
    behavior: { instruction: persona.behavior },
    trigger_preferences: [...persona.triggerPreferences],
    avoid_patterns: [...persona.avoidPatterns],
    silence_bias: normalizeBias(persona.silenceBias),
    burst_bias: normalizeBias(persona.burstBias),
    repetition_bias: normalizeBias(persona.repetitionBias),
    cooldown_ms: persona.cooldownMs,
    content_flags: [...persona.contentFlags],
    enabled: persona.enabled
  }
}

function compileMode(mode: AudienceMode): CanonicalModeDefinition {
  return {
    mode_id: mode.id,
    namespace_id: mode.namespaceId,
    revision: mode.revision,
    persona_counts: Object.fromEntries(
      Object.keys(mode.personaCounts).sort().map((personaId) => [
        personaId,
        mode.personaCounts[personaId]
      ])
    ),
    persona_overrides: Object.fromEntries(
      Object.entries(mode.personaOverrides).map(([personaId, override]) => [
        personaId,
        compilePersonaOverride(override)
      ])
    ),
    normal_response_range: {
      minimum: mode.normalResponseRange[0],
      maximum: mode.normalResponseRange[1]
    },
    highlight_response_range: {
      minimum: mode.highlightResponseRange[0],
      maximum: mode.highlightResponseRange[1]
    },
    ambience: mode.ambience
  }
}

function compilePersonaOverride(override: PersonaOverride): Record<string, unknown> {
  return compact({
    display_name: override.name,
    traits: override.traits ? [...override.traits] : undefined,
    speech_style: override.speechStyle === undefined
      ? undefined
      : { instruction: override.speechStyle },
    behavior: override.behavior === undefined ? undefined : { instruction: override.behavior },
    trigger_preferences: override.triggerPreferences
      ? [...override.triggerPreferences]
      : undefined,
    avoid_patterns: override.avoidPatterns ? [...override.avoidPatterns] : undefined,
    silence_bias: override.silenceBias === undefined
      ? undefined
      : normalizeBias(override.silenceBias),
    burst_bias: override.burstBias === undefined ? undefined : normalizeBias(override.burstBias),
    repetition_bias: override.repetitionBias === undefined
      ? undefined
      : normalizeBias(override.repetitionBias),
    cooldown_ms: override.cooldownMs,
    content_flags: override.contentFlags ? [...override.contentFlags] : undefined
  })
}

function normalizeBias(value: number): number {
  return value / 4
}

function compact(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined))
}

function serializeCanonical(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'string') return JSON.stringify(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new Error('canonical JSON numbers must be finite')
    }
    // JSON.stringify supplies ECMAScript's shortest round-trip binary64 form.
    return JSON.stringify(Object.is(value, -0) ? 0 : value)
  }
  if (Array.isArray(value)) {
    return `[${value.map(serializeCanonical).join(',')}]`
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => compareUtf16CodeUnits(left, right))
      .map(([key, item]) => `${JSON.stringify(key)}:${serializeCanonical(item)}`)
    return `{${entries.join(',')}}`
  }
  throw new Error(`Unsupported canonical JSON value: ${typeof value}`)
}

function compareUtf16CodeUnits(left: string, right: string): number {
  const sharedLength = Math.min(left.length, right.length)
  for (let index = 0; index < sharedLength; index += 1) {
    const difference = left.charCodeAt(index) - right.charCodeAt(index)
    if (difference !== 0) return difference
  }
  return left.length - right.length
}
