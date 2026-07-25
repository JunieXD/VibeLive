import { clonePersonaTemplate, createPersonaTemplate } from './canonical'
import { validatePersona } from './persona-markdown'
import {
  BASE_PERSONAS,
  BUILT_IN_MODES,
  DEFAULT_DISPATCH_SETTINGS,
  DEFAULT_VISUAL_SETTINGS
} from './presets'
import type {
  AudienceDispatchSettings,
  AudienceMode,
  AudienceVisualSettings,
  AudienceWorkspaceState,
  PersonaOverride,
  PersonaTemplate
} from './types'

const STABLE_ID_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/
const MODE_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const MAX_FRAME_BUNDLE_SIZE = 5
const MAX_FRAME_WINDOW_MS = 30_000
type AudienceWorkspaceSourceVersion = 1 | 2 | 3 | 4 | 5 | 6 | 7
const BUILT_IN_MODE_MIGRATIONS = new Map<
  string,
  { readonly fromRevisions: readonly number[]; readonly toRevision: number }
>([
  ['room-6657', { fromRevisions: [1, 2], toRevision: 3 }],
  ['music-live-room', { fromRevisions: [1], toRevision: 2 }],
  ['chat-story-room', { fromRevisions: [1], toRevision: 2 }],
  ['creative-studio', { fromRevisions: [1], toRevision: 2 }],
  ['food-life-room', { fromRevisions: [1], toRevision: 2 }],
  ['travel-outdoor-room', { fromRevisions: [1], toRevision: 2 }],
  ['sports-watch-party', { fromRevisions: [1], toRevision: 2 }]
])
const BUILT_IN_MODE_LEGACY_NAMES = new Map([
  ['lively-game-room', '热闹游戏房'],
  ['room-6657', '6657 玩机器风格'],
  ['newcomer-friendly', '新人友好'],
  ['gentle-company', '温和陪伴'],
  ['competitive-banter', '竞技嘴硬局'],
  ['just-for-laughs', '纯乐子冷场包']
])

export type AudienceWorkspaceParseResult =
  | {
      readonly ok: true
      readonly workspace: AudienceWorkspaceState
      readonly migratedFromVersion?: 1 | 2 | 3 | 4 | 5 | 6
      readonly legacyMemes?: readonly LegacyLocalMeme[]
    }
  | { readonly ok: false; readonly issues: readonly string[] }

export type LegacyLocalMeme = {
  readonly id: string
  readonly text: string
  readonly createdAt: string | null
}

export function parseAudienceWorkspaceState(value: unknown): AudienceWorkspaceParseResult {
  if (!isRecord(value)) return { ok: false, issues: ['workspace must be an object'] }
  if (
    value.version !== 1 &&
    value.version !== 2 &&
    value.version !== 3 &&
    value.version !== 4 &&
    value.version !== 5 &&
    value.version !== 6 &&
    value.version !== 7
  ) {
    return { ok: false, issues: ['version must be 1, 2, 3, 4, 5, 6 or 7'] }
  }
  const sourceVersion = value.version
  const issues: string[] = []
  const personas = parsePersonas(value.personas, sourceVersion, issues)
  const legacyMemes = sourceVersion === 1
    ? parseLegacyMemes(value.memes, issues)
    : []
  if (sourceVersion >= 2 && Array.isArray(value.memes) && value.memes.length > 0) {
    issues.push('legacy local memes require Shared Brain migration and were not loaded')
  } else if (
    sourceVersion >= 2 &&
    value.memes !== undefined &&
    !Array.isArray(value.memes)
  ) {
    issues.push('legacy memes must be an array when present')
  }
  const modeStateValue = value.modeState
  const modes = isRecord(modeStateValue)
    ? upgradeBuiltInModes(
        parseArray(
          modeStateValue.modes,
          'modeState.modes',
          (item, path, nestedIssues) => parseMode(item, path, sourceVersion, nestedIssues),
          issues
        )
      )
    : (issues.push('modeState must be an object'), [])
  const activeModeId = isRecord(modeStateValue) && typeof modeStateValue.activeModeId === 'string'
    ? modeStateValue.activeModeId
    : (issues.push('modeState.activeModeId must be a string'), '')

  validateReferences(personas, modes, activeModeId, issues)
  if (issues.length > 0) return { ok: false, issues }
  const migratedFromVersion: 1 | 2 | 3 | 4 | 5 | 6 | undefined = sourceVersion === 7
    ? undefined
    : sourceVersion
  return {
    ok: true,
    workspace: { version: 7, personas, modeState: { modes, activeModeId } },
    ...(migratedFromVersion === undefined ? {} : { migratedFromVersion }),
    ...(legacyMemes.length > 0 ? { legacyMemes } : {})
  }
}

function parseLegacyMemes(value: unknown, issues: string[]): LegacyLocalMeme[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) {
    issues.push('legacy memes must be an array when present')
    return []
  }
  return value.flatMap((item, index) => {
    const path = `memes[${index}]`
    if (!isRecord(item)) {
      issues.push(`${path} must be an object`)
      return []
    }
    if (typeof item.id !== 'string' || !item.id.trim()) {
      issues.push(`${path}.id must be a non-empty string`)
    }
    if (typeof item.text !== 'string' || !item.text.trim()) {
      issues.push(`${path}.text must be a non-empty string`)
    }
    if (
      item.createdAt !== undefined &&
      (typeof item.createdAt !== 'string' || !Number.isFinite(Date.parse(item.createdAt)))
    ) {
      issues.push(`${path}.createdAt must be a valid date when present`)
    }
    if (issues.some((issue) => issue.startsWith(path))) return []
    return [{
      id: (item.id as string).trim(),
      text: (item.text as string).trim(),
      createdAt: typeof item.createdAt === 'string' ? item.createdAt : null
    }]
  })
}

function parsePersonas(
  value: unknown,
  sourceVersion: AudienceWorkspaceSourceVersion,
  issues: string[]
): PersonaTemplate[] {
  const builtInIds = new Set(BASE_PERSONAS.map((persona) => persona.id))
  const builtIns = BASE_PERSONAS.map(clonePersonaTemplate)
  if (!Array.isArray(value)) {
    issues.push('personas must be an array')
    return builtIns
  }
  const customPersonas = value.flatMap((item, index) => {
    if (isRecord(item) && typeof item.id === 'string' && builtInIds.has(item.id)) return []
    const parsed = parsePersona(item, `personas[${index}]`, sourceVersion, issues)
    return parsed ? [parsed] : []
  })
  return [...builtIns, ...customPersonas]
}

function parsePersona(
  value: unknown,
  path: string,
  sourceVersion: AudienceWorkspaceSourceVersion,
  issues: string[]
): PersonaTemplate | null {
  if (!isRecord(value)) return fail(path, 'must be an object', issues)
  let candidate: PersonaTemplate
  try {
    const hasAnyTemplateMetadata = ['documentVersion', 'revision', 'contentHash'].some(
      (field) => Object.hasOwn(value, field)
    )
    candidate = sourceVersion === 1 || !hasAnyTemplateMetadata
      ? createPersonaTemplate(value as unknown as Parameters<typeof createPersonaTemplate>[0])
      : value as unknown as PersonaTemplate
  } catch {
    issues.push(`${path} is not a valid persona`)
    return null
  }
  const personaIssues = validatePersona(candidate)
  if (personaIssues.length > 0) {
    issues.push(...personaIssues.map((issue) => `${path}.${issue.field}: ${issue.message}`))
    return null
  }
  return clonePersonaTemplate(candidate)
}

function parseMode(
  value: unknown,
  path: string,
  sourceVersion: AudienceWorkspaceSourceVersion,
  issues: string[]
): AudienceMode | null {
  if (!isRecord(value)) return fail(path, 'must be an object', issues)
  for (const field of ['id', 'name', 'description'] as const) {
    if (typeof value[field] !== 'string' || !value[field].trim()) {
      issues.push(`${path}.${field} must be a non-empty string`)
    }
  }
  if (typeof value.id === 'string' && !MODE_ID_PATTERN.test(value.id)) {
    issues.push(`${path}.id must be a stable kebab-case identifier`)
  }
  if (typeof value.builtIn !== 'boolean') issues.push(`${path}.builtIn must be boolean`)

  const legacyPersonaIds = sourceVersion < 4
    ? stringArray(value.personaIds, `${path}.personaIds`, issues)
    : []
  if (new Set(legacyPersonaIds).size !== legacyPersonaIds.length) {
    issues.push(`${path}.personaIds must be unique`)
  }
  const personaOverrides = parsePersonaOverrides(value.personaOverrides, path, issues)
  const legacyBase = sourceVersion === 1
    ? integerRange(value.baseActivity, `${path}.baseActivity`, issues)
    : [0, 0] as const
  const legacyBurst = sourceVersion === 1
    ? integerRange(value.burstLimit, `${path}.burstLimit`, issues)
    : [0, 0] as const
  const normalResponseRange = sourceVersion === 1
    ? legacyBase
    : integerRange(value.normalResponseRange, `${path}.normalResponseRange`, issues)
  const highlightResponseRange = sourceVersion === 1
    ? legacyBurst
    : integerRange(value.highlightResponseRange, `${path}.highlightResponseRange`, issues)
  const legacyTargetConcurrentViewers = sourceVersion === 1
    ? clamp(legacyBurst[1], 1, 32)
    : sourceVersion === 2
      ? boundedInteger(value.viewerCount, `${path}.viewerCount`, 1, 32, issues)
      : sourceVersion === 3
        ? boundedInteger(
          value.targetConcurrentViewers,
          `${path}.targetConcurrentViewers`,
          1,
          32,
          issues
        )
        : 0
  const personaCounts = sourceVersion >= 4
    ? parsePersonaCounts(value.personaCounts, path, issues)
    : allocateLegacyPersonaCounts(
        legacyPersonaIds,
        parsePersonaWeights(value.personaWeights, legacyPersonaIds, path, issues),
        legacyTargetConcurrentViewers
      )
  const viewerCount = Object.values(personaCounts).reduce((total, count) => total + count, 0)
  if (viewerCount < 1 || viewerCount > 32) {
    issues.push(`${path}.personaCounts must add up to an integer from 1 to 32`)
  }
  if (normalResponseRange[1] > viewerCount) {
    issues.push(`${path}.normalResponseRange maximum cannot exceed the total persona count`)
  }
  if (highlightResponseRange[1] > viewerCount) {
    issues.push(`${path}.highlightResponseRange maximum cannot exceed the total persona count`)
  }
  if (value.ambience !== 'natural' && value.ambience !== 'continuous') {
    issues.push(`${path}.ambience must be natural or continuous`)
  }
  const namespaceId = sourceVersion === 1
    ? value.id as string
    : nonEmptyStableId(value.namespaceId, `${path}.namespaceId`, issues)
  const revision = sourceVersion === 1
    ? 1
    : boundedInteger(value.revision, `${path}.revision`, 1, Number.MAX_SAFE_INTEGER, issues)
  const parsedVisualSettings = sourceVersion === 1
    ? { ...DEFAULT_VISUAL_SETTINGS }
    : parseVisualSettings(value.visualSettings, `${path}.visualSettings`, issues)
  // Version 3 originally shipped this exact default as a short 3-frame
  // window. It was never a deliberate per-room choice, so upgrade it.
  const visualSettings = isLegacyVisualDefault(parsedVisualSettings)
    ? { ...DEFAULT_VISUAL_SETTINGS }
    : parsedVisualSettings
  const dispatchSettings = sourceVersion < 6
    ? { ...DEFAULT_DISPATCH_SETTINGS }
    : parseDispatchSettings(
        value.dispatchSettings,
        `${path}.dispatchSettings`,
        sourceVersion,
        issues
      )

  if (issues.some((issue) => issue.startsWith(path))) return null
  return {
    id: value.id as string,
    namespaceId,
    revision,
    name: value.name as string,
    description: value.description as string,
    builtIn: value.builtIn as boolean,
    personaCounts,
    personaOverrides,
    normalResponseRange,
    highlightResponseRange,
    ambience: value.ambience as AudienceMode['ambience'],
    visualSettings,
    dispatchSettings,
    baseActivity: normalResponseRange,
    burstLimit: highlightResponseRange
  }
}

function upgradeBuiltInModes(modes: readonly AudienceMode[]): AudienceMode[] {
  const currentBuiltIns = new Map(BUILT_IN_MODES.map((mode) => [mode.id, mode]))
  const upgraded = modes.map((mode) => {
    const current = currentBuiltIns.get(mode.id)
    const migration = BUILT_IN_MODE_MIGRATIONS.get(mode.id)
    if (
      mode.builtIn &&
      current &&
      migration &&
      migration.fromRevisions.includes(mode.revision) &&
      current.revision === migration.toRevision
    ) {
      return cloneAudienceMode(current)
    }
    const legacyName = BUILT_IN_MODE_LEGACY_NAMES.get(mode.id)
    return mode.builtIn && current && mode.name === legacyName
      ? { ...mode, name: current.name }
      : mode
  })
  const knownModeIds = new Set(upgraded.map((mode) => mode.id))
  return [
    ...upgraded,
    ...BUILT_IN_MODES
      .filter((mode) => !knownModeIds.has(mode.id))
      .map(cloneAudienceMode)
  ]
}

function isLegacyVisualDefault(settings: AudienceVisualSettings): boolean {
  return settings.barrageGenerationMode === 'per_viewer' &&
    settings.viewerVisualInputMode === 'direct_frames' &&
    settings.frameBundleSize === 3 &&
    settings.frameWindowMs === 10_000 &&
    settings.frameSelectionStrategy === 'change_peaks'
}

function parsePersonaWeights(
  value: unknown,
  personaIds: readonly string[],
  path: string,
  issues: string[]
): Record<string, number> {
  if (!isRecord(value)) {
    issues.push(`${path}.personaWeights must be an object`)
    return {}
  }
  const weights: Record<string, number> = {}
  for (const [personaId, weight] of Object.entries(value)) {
    if (!personaIds.includes(personaId) || typeof weight !== 'number' ||
      !Number.isFinite(weight) || weight < 0) {
      issues.push(`${path}.personaWeights.${personaId} must be a non-negative weight for a mode persona`)
      continue
    }
    weights[personaId] = weight
  }
  for (const personaId of personaIds) {
    if (!(personaId in weights)) issues.push(`${path}.personaWeights.${personaId} is required`)
  }
  if (!Object.values(weights).some((weight) => weight > 0)) {
    issues.push(`${path}.personaWeights must contain a positive weight`)
  }
  return weights
}

function parsePersonaCounts(
  value: unknown,
  path: string,
  issues: string[]
): Record<string, number> {
  if (!isRecord(value)) {
    issues.push(`${path}.personaCounts must be an object`)
    return {}
  }
  const counts: Record<string, number> = {}
  for (const [personaId, count] of Object.entries(value)) {
    if (
      !STABLE_ID_PATTERN.test(personaId) ||
      typeof count !== 'number' ||
      !Number.isInteger(count) ||
      count < 0 ||
      count > 32
    ) {
      issues.push(`${path}.personaCounts.${personaId} must be an integer from 0 to 32`)
      continue
    }
    counts[personaId] = count
  }
  if (Object.keys(counts).length === 0) {
    issues.push(`${path}.personaCounts must contain at least one persona`)
  }
  return counts
}

function allocateLegacyPersonaCounts(
  personaIds: readonly string[],
  weights: Readonly<Record<string, number>>,
  target: number
): Record<string, number> {
  const eligible = personaIds
    .map((personaId, index) => ({ personaId, index, weight: weights[personaId] ?? 0 }))
    .filter((item) => item.weight > 0)
  const totalWeight = eligible.reduce((total, item) => total + item.weight, 0)
  if (totalWeight <= 0) return Object.fromEntries(personaIds.map((personaId) => [personaId, 0]))
  const allocations = eligible.map((item) => {
    const exact = target * item.weight / totalWeight
    return { ...item, count: Math.floor(exact), remainder: exact - Math.floor(exact) }
  })
  const remaining = target - allocations.reduce((total, item) => total + item.count, 0)
  for (const item of [...allocations]
    .sort((left, right) =>
      right.remainder - left.remainder || left.index - right.index ||
      left.personaId.localeCompare(right.personaId)
    )
    .slice(0, remaining)) {
    item.count += 1
  }
  const counts = Object.fromEntries(personaIds.map((personaId) => [personaId, 0]))
  for (const item of allocations) counts[item.personaId] = item.count
  return counts
}

function parsePersonaOverrides(
  value: unknown,
  path: string,
  issues: string[]
): Record<string, PersonaOverride> {
  if (!isRecord(value)) {
    issues.push(`${path}.personaOverrides must be an object`)
    return {}
  }
  const overrides: Record<string, PersonaOverride> = {}
  for (const [personaId, override] of Object.entries(value)) {
    if (!STABLE_ID_PATTERN.test(personaId) || !isRecord(override)) {
      issues.push(`${path}.personaOverrides.${personaId} must target a stable persona id`)
      continue
    }
    validatePersonaOverride(override, `${path}.personaOverrides.${personaId}`, issues)
    overrides[personaId] = clonePersonaOverride(override)
  }
  return overrides
}

function parseVisualSettings(
  value: unknown,
  path: string,
  issues: string[]
): AudienceVisualSettings {
  if (!isRecord(value)) {
    issues.push(`${path} must be an object`)
    return { ...DEFAULT_VISUAL_SETTINGS }
  }
  let barrageGenerationMode: AudienceVisualSettings['barrageGenerationMode'] = 'per_viewer'
  if (value.barrageGenerationMode === 'window_batch') {
    barrageGenerationMode = 'window_batch'
  } else if (
    value.barrageGenerationMode !== undefined &&
    value.barrageGenerationMode !== 'per_viewer'
  ) {
    issues.push(`${path}.barrageGenerationMode is invalid`)
  }
  if (value.viewerVisualInputMode !== 'direct_frames' &&
    value.viewerVisualInputMode !== 'shared_summary' &&
    value.viewerVisualInputMode !== 'text_only') {
    issues.push(`${path}.viewerVisualInputMode is invalid`)
  }
  if (!['latest_n', 'evenly_spaced', 'change_peaks'].includes(
    value.frameSelectionStrategy as string
  )) {
    issues.push(`${path}.frameSelectionStrategy is invalid`)
  }
  const frameBundleSize = legacyFrameBundleSize(value.frameBundleSize) ?? boundedInteger(
    value.frameBundleSize,
    `${path}.frameBundleSize`,
    1,
    MAX_FRAME_BUNDLE_SIZE,
    issues
  )
  const frameWindowMs = legacyFrameWindowMs(value.frameWindowMs) ?? boundedInteger(
    value.frameWindowMs,
    `${path}.frameWindowMs`,
    1,
    MAX_FRAME_WINDOW_MS,
    issues
  )
  const frameMaxDimension = boundedInteger(
    value.frameMaxDimension,
    `${path}.frameMaxDimension`,
    64,
    4096,
    issues
  )
  if (typeof value.frameQuality !== 'number' || !Number.isFinite(value.frameQuality) ||
    value.frameQuality <= 0 || value.frameQuality > 1) {
    issues.push(`${path}.frameQuality must be greater than 0 and at most 1`)
  }
  const frameSelectionStrategy = value.frameSelectionStrategy === 'evenly_spaced'
    ? 'latest_n'
    : value.frameSelectionStrategy as AudienceVisualSettings['frameSelectionStrategy']
  return {
    barrageGenerationMode,
    viewerVisualInputMode: barrageGenerationMode === 'window_batch'
      ? 'direct_frames'
      : value.viewerVisualInputMode as AudienceVisualSettings['viewerVisualInputMode'],
    frameBundleSize: barrageGenerationMode === 'window_batch' ? 4 : frameBundleSize,
    frameWindowMs: barrageGenerationMode === 'window_batch' ? 30_000 : frameWindowMs,
    frameSelectionStrategy: barrageGenerationMode === 'window_batch'
      ? 'change_peaks'
      : frameSelectionStrategy,
    frameMaxDimension: barrageGenerationMode === 'window_batch'
      ? Math.min(frameMaxDimension, 768)
      : frameMaxDimension,
    frameQuality: barrageGenerationMode === 'window_batch'
      ? Math.min(value.frameQuality as number, 0.7)
      : value.frameQuality as number
  }
}

function parseDispatchSettings(
  value: unknown,
  path: string,
  sourceVersion: AudienceWorkspaceSourceVersion,
  issues: string[]
): AudienceDispatchSettings {
  if (!isRecord(value)) {
    issues.push(`${path} must be an object`)
    return { ...DEFAULT_DISPATCH_SETTINGS }
  }
  const allowViewerSilence = sourceVersion < 7
    ? false
    : value.allowViewerSilence
  if (typeof allowViewerSilence !== 'boolean') {
    issues.push(`${path}.allowViewerSilence must be boolean`)
  }
  return {
    allowViewerSilence: allowViewerSilence === true,
    userSpeakerBudget: boundedInteger(
      value.userSpeakerBudget,
      `${path}.userSpeakerBudget`,
      0,
      32,
      issues
    ),
    screenSpeakerBudget: boundedInteger(
      value.screenSpeakerBudget,
      `${path}.screenSpeakerBudget`,
      0,
      32,
      issues
    ),
    ambientSpeakerBudget: boundedInteger(
      value.ambientSpeakerBudget,
      `${path}.ambientSpeakerBudget`,
      0,
      32,
      issues
    ),
    maxInFlightViewerRequests: boundedInteger(
      value.maxInFlightViewerRequests,
      `${path}.maxInFlightViewerRequests`,
      1,
      32,
      issues
    ),
    viewerRequestStartIntervalMs: boundedInteger(
      value.viewerRequestStartIntervalMs,
      `${path}.viewerRequestStartIntervalMs`,
      0,
      60_000,
      issues
    ),
    viewerQueueCapacity: boundedInteger(
      value.viewerQueueCapacity,
      `${path}.viewerQueueCapacity`,
      1,
      65_536,
      issues
    ),
    ambientTickCooldownMs: boundedInteger(
      value.ambientTickCooldownMs,
      `${path}.ambientTickCooldownMs`,
      1,
      Number.MAX_SAFE_INTEGER,
      issues
    ),
    maxConsecutiveAmbientWaves: boundedInteger(
      value.maxConsecutiveAmbientWaves,
      `${path}.maxConsecutiveAmbientWaves`,
      0,
      32,
      issues
    )
  }
}

function legacyFrameBundleSize(value: unknown): number | null {
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value <= MAX_FRAME_BUNDLE_SIZE ||
    value > 60
  ) {
    return null
  }
  return MAX_FRAME_BUNDLE_SIZE
}

function legacyFrameWindowMs(value: unknown): number | null {
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value <= MAX_FRAME_WINDOW_MS ||
    value > 300_000
  ) {
    return null
  }
  return MAX_FRAME_WINDOW_MS
}

function validateReferences(
  personas: readonly PersonaTemplate[],
  modes: readonly AudienceMode[],
  activeModeId: string,
  issues: string[]
): void {
  const personaIds = new Set(personas.map((persona) => persona.id))
  const personasById = new Map(personas.map((persona) => [persona.id, persona]))
  if (personaIds.size !== personas.length) issues.push('persona ids must be unique')
  const modeIds = new Set(modes.map((mode) => mode.id))
  const builtInModeIds = new Set(BUILT_IN_MODES.map((mode) => mode.id))
  if (modeIds.size !== modes.length) issues.push('mode ids must be unique')
  for (const builtInModeId of builtInModeIds) {
    if (!modeIds.has(builtInModeId)) issues.push(`built-in mode ${builtInModeId} is missing`)
  }
  if (!modeIds.has(activeModeId)) issues.push('activeModeId must reference an existing mode')
  for (const mode of modes) {
    if (mode.builtIn !== builtInModeIds.has(mode.id)) {
      issues.push(`mode ${mode.id} has an invalid builtIn identity`)
    }
    for (const personaId of Object.keys(mode.personaCounts)) {
      if (!personaIds.has(personaId)) issues.push(`mode ${mode.id} references unknown persona ${personaId}`)
    }
    const viewerCount = Object.values(mode.personaCounts).reduce((total, count) => total + count, 0)
    if (viewerCount < 1 || viewerCount > 32) {
      issues.push(`mode ${mode.id} must assign from 1 to 32 viewers`)
    }
    for (const [personaId, count] of Object.entries(mode.personaCounts)) {
      if (count > 0 && !personasById.get(personaId)?.enabled) {
        issues.push(`mode ${mode.id} assigns viewers to disabled persona ${personaId}`)
      }
    }
    for (const [personaId, override] of Object.entries(mode.personaOverrides)) {
      const base = personasById.get(personaId)
      if (!base) {
        issues.push(`mode ${mode.id} override references unknown persona ${personaId}`)
        continue
      }
      const effective = createPersonaTemplate({
        ...base,
        ...override,
        revision: base.revision
      })
      const contentIssues = validatePersona(effective).filter(
        (issue) => issue.field !== 'contentHash'
      )
      issues.push(...contentIssues.map(
        (issue) => `mode ${mode.id} override ${personaId}.${issue.field}: ${issue.message}`
      ))
    }
  }
}

function validatePersonaOverride(
  override: Record<string, unknown>,
  path: string,
  issues: string[]
): void {
  const allowed = new Set([
    'name', 'initials', 'role', 'color', 'traits', 'speechStyle', 'behavior',
    'triggerPreferences', 'avoidPatterns', 'silenceBias', 'burstBias', 'repetitionBias',
    'cooldownMs', 'maxCommentsPerDecision', 'contentFlags', 'enabled'
  ])
  for (const key of Object.keys(override)) {
    if (!allowed.has(key)) issues.push(`${path}.${key} is not an editable persona field`)
  }
  for (const field of ['name', 'initials', 'role', 'color', 'speechStyle', 'behavior'] as const) {
    if (field in override && typeof override[field] !== 'string') {
      issues.push(`${path}.${field} must be a string`)
    }
  }
  for (const field of ['traits', 'triggerPreferences', 'avoidPatterns', 'contentFlags'] as const) {
    if (field in override) stringArray(override[field], `${path}.${field}`, issues)
  }
  for (const field of ['silenceBias', 'burstBias', 'repetitionBias'] as const) {
    const value = override[field]
    if (field in override && (typeof value !== 'number' || !Number.isInteger(value) ||
      value < 0 || value > 4)) {
      issues.push(`${path}.${field} must be an integer from 0 to 4`)
    }
  }
  if ('cooldownMs' in override && (typeof override.cooldownMs !== 'number' ||
    !Number.isInteger(override.cooldownMs) || override.cooldownMs < 0)) {
    issues.push(`${path}.cooldownMs must be a non-negative integer`)
  }
  if ('maxCommentsPerDecision' in override &&
    override.maxCommentsPerDecision !== 1 && override.maxCommentsPerDecision !== 2) {
    issues.push(`${path}.maxCommentsPerDecision must be 1 or 2`)
  }
  if ('enabled' in override && typeof override.enabled !== 'boolean') {
    issues.push(`${path}.enabled must be boolean`)
  }
}

function clonePersonaOverride(override: Record<string, unknown>): PersonaOverride {
  const clone = { ...override }
  for (const field of ['traits', 'triggerPreferences', 'avoidPatterns', 'contentFlags'] as const) {
    if (Array.isArray(clone[field])) clone[field] = [...clone[field]]
  }
  return clone as PersonaOverride
}

function cloneAudienceMode(mode: AudienceMode): AudienceMode {
  return {
    ...mode,
    personaCounts: { ...mode.personaCounts },
    personaOverrides: Object.fromEntries(
      Object.entries(mode.personaOverrides).map(([personaId, override]) => [
        personaId,
        clonePersonaOverride(override as Record<string, unknown>)
      ])
    ),
    normalResponseRange: [...mode.normalResponseRange],
    highlightResponseRange: [...mode.highlightResponseRange],
    visualSettings: { ...mode.visualSettings },
    dispatchSettings: { ...mode.dispatchSettings },
    baseActivity: [...mode.baseActivity],
    burstLimit: [...mode.burstLimit]
  }
}

function parseArray<T>(
  value: unknown,
  path: string,
  parser: (item: unknown, path: string, issues: string[]) => T | null,
  issues: string[]
): T[] {
  if (!Array.isArray(value)) {
    issues.push(`${path} must be an array`)
    return []
  }
  return value.flatMap((item, index) => {
    const parsed = parser(item, `${path}[${index}]`, issues)
    return parsed ? [parsed] : []
  })
}

function stringArray(value: unknown, path: string, issues: string[]): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    issues.push(`${path} must be a string array`)
    return []
  }
  return [...value]
}

function integerRange(value: unknown, path: string, issues: string[]): readonly [number, number] {
  if (!Array.isArray(value) || value.length !== 2 || value.some((item) =>
    typeof item !== 'number' || !Number.isInteger(item)) ||
    value[0] < 0 || value[1] < value[0]) {
    issues.push(`${path} must be an ascending non-negative integer pair`)
    return [0, 0]
  }
  return [value[0] as number, value[1] as number]
}

function boundedInteger(
  value: unknown,
  path: string,
  minimum: number,
  maximum: number,
  issues: string[]
): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < minimum || value > maximum) {
    issues.push(`${path} must be an integer from ${minimum} to ${maximum}`)
    return minimum
  }
  return value
}

function nonEmptyStableId(value: unknown, path: string, issues: string[]): string {
  if (typeof value !== 'string' || !MODE_ID_PATTERN.test(value)) {
    issues.push(`${path} must be a stable kebab-case identifier`)
    return ''
  }
  return value
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fail<T>(path: string, message: string, issues: string[]): T | null {
  issues.push(`${path} ${message}`)
  return null
}
