import { validatePersona } from './persona-markdown'
import { findMemeConflict, normalizeMemeText } from './memes'
import { BASE_PERSONAS, BUILT_IN_MODES } from './presets'
import type {
  AudienceMode,
  AudienceWorkspaceState,
  MemeEntry,
  Persona,
  PersonaOverride
} from './types'

const STABLE_ID_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/
const MODE_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/

export type AudienceWorkspaceParseResult =
  | { readonly ok: true; readonly workspace: AudienceWorkspaceState }
  | { readonly ok: false; readonly issues: readonly string[] }

export function parseAudienceWorkspaceState(value: unknown): AudienceWorkspaceParseResult {
  const issues: string[] = []
  if (!isRecord(value)) return { ok: false, issues: ['workspace must be an object'] }
  if (value.version !== 1) issues.push('version must be 1')

  const personas = parsePersonas(value.personas, issues)
  const memes = parseArray(value.memes, 'memes', parseMeme, issues)
  const modeStateValue = value.modeState
  const modes = isRecord(modeStateValue)
    ? parseArray(modeStateValue.modes, 'modeState.modes', parseMode, issues)
    : (issues.push('modeState must be an object'), [])
  const activeModeId = isRecord(modeStateValue) && typeof modeStateValue.activeModeId === 'string'
    ? modeStateValue.activeModeId
    : (issues.push('modeState.activeModeId must be a string'), '')

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
    for (const personaId of mode.personaIds) {
      if (!personaIds.has(personaId)) issues.push(`mode ${mode.id} references unknown persona ${personaId}`)
    }
    for (const [personaId, override] of Object.entries(mode.personaOverrides)) {
      const base = personasById.get(personaId)
      if (!base) {
        issues.push(`mode ${mode.id} override references unknown persona ${personaId}`)
        continue
      }
      const effective = { ...base, ...override, id: base.id } as Persona
      issues.push(
        ...validatePersona(effective).map(
          (issue) => `mode ${mode.id} override ${personaId}.${issue.field}: ${issue.message}`
        )
      )
    }
  }
  if (new Set(memes.map((meme) => meme.id)).size !== memes.length) issues.push('meme ids must be unique')
  for (const [index, meme] of memes.entries()) {
    if (!modeIds.has(meme.modeId)) issues.push(`meme ${meme.id} references unknown mode ${meme.modeId}`)
    if (meme.status !== 'archived') {
      const conflict = findMemeConflict(memes.slice(0, index), meme)
      if (conflict) issues.push(`meme ${meme.id} conflicts with an existing ${conflict} entry`)
    }
  }

  if (issues.length > 0) return { ok: false, issues }
  return {
    ok: true,
    workspace: { version: 1, personas, modeState: { modes, activeModeId }, memes }
  }
}

function parsePersonas(value: unknown, issues: string[]): Persona[] {
  const builtInIds = new Set(BASE_PERSONAS.map((persona) => persona.id))
  const builtIns = BASE_PERSONAS.map(clonePersona)
  if (!Array.isArray(value)) {
    issues.push('personas must be an array')
    return builtIns
  }

  const customPersonas = value.flatMap((item, index) => {
    if (isRecord(item) && typeof item.id === 'string' && builtInIds.has(item.id)) return []
    const parsed = parsePersona(item, `personas[${index}]`, issues)
    return parsed ? [parsed] : []
  })
  return [...builtIns, ...customPersonas]
}

function clonePersona(persona: Persona): Persona {
  return {
    ...persona,
    traits: [...persona.traits],
    triggerPreferences: [...persona.triggerPreferences],
    avoidPatterns: [...persona.avoidPatterns],
    contentFlags: [...persona.contentFlags]
  }
}

function parsePersona(value: unknown, path: string, issues: string[]): Persona | null {
  if (!isRecord(value)) return fail(path, 'must be an object', issues)
  const persona = value as unknown as Persona
  const personaIssues = validatePersona(persona)
  if (personaIssues.length > 0) {
    issues.push(...personaIssues.map((issue) => `${path}.${issue.field}: ${issue.message}`))
    return null
  }
  return {
    ...persona,
    traits: [...persona.traits],
    triggerPreferences: [...persona.triggerPreferences],
    avoidPatterns: [...persona.avoidPatterns],
    contentFlags: [...persona.contentFlags]
  }
}

function parseMode(value: unknown, path: string, issues: string[]): AudienceMode | null {
  if (!isRecord(value)) return fail(path, 'must be an object', issues)
  const stringFields = ['id', 'name', 'description'] as const
  for (const field of stringFields) {
    if (typeof value[field] !== 'string' || !value[field].trim()) {
      issues.push(`${path}.${field} must be a non-empty string`)
    }
  }
  if (typeof value.id === 'string' && !MODE_ID_PATTERN.test(value.id)) {
    issues.push(`${path}.id must be a stable kebab-case identifier`)
  }
  if (typeof value.builtIn !== 'boolean') issues.push(`${path}.builtIn must be boolean`)
  const personaIds = stringArray(value.personaIds, `${path}.personaIds`, issues)
  if (new Set(personaIds).size !== personaIds.length) {
    issues.push(`${path}.personaIds must be unique`)
  }
  if (!isRecord(value.personaWeights)) issues.push(`${path}.personaWeights must be an object`)
  const personaWeights: Record<string, number> = isRecord(value.personaWeights)
    ? Object.fromEntries(Object.entries(value.personaWeights).filter((entry): entry is [string, number] => {
      const [id, weight] = entry
      const valid =
        personaIds.includes(id) &&
        typeof weight === 'number' &&
        Number.isFinite(weight) &&
        weight > 0
      if (!valid) issues.push(`${path}.personaWeights.${id} must be a positive weight for a mode persona`)
      return valid
    }))
    : {}
  for (const personaId of personaIds) {
    if (!(personaId in personaWeights)) issues.push(`${path}.personaWeights.${personaId} is required`)
  }
  if (!isRecord(value.personaOverrides)) issues.push(`${path}.personaOverrides must be an object`)
  const personaOverrides: Record<string, PersonaOverride> = {}
  for (const [personaId, override] of Object.entries(
    isRecord(value.personaOverrides) ? value.personaOverrides : {}
  )) {
    if (!STABLE_ID_PATTERN.test(personaId) || !isRecord(override)) {
      issues.push(`${path}.personaOverrides.${personaId} must target a stable persona id`)
      continue
    }
    validatePersonaOverride(override, `${path}.personaOverrides.${personaId}`, issues)
    personaOverrides[personaId] = clonePersonaOverride(override)
  }
  const baseActivity = numberRange(value.baseActivity, `${path}.baseActivity`, issues)
  const burstLimit = numberRange(value.burstLimit, `${path}.burstLimit`, issues)
  if (value.ambience !== 'natural' && value.ambience !== 'continuous') {
    issues.push(`${path}.ambience must be natural or continuous`)
  }
  if (issues.some((issue) => issue.startsWith(path))) return null
  return {
    id: value.id as string,
    name: value.name as string,
    description: value.description as string,
    builtIn: value.builtIn as boolean,
    personaIds,
    personaWeights,
    personaOverrides,
    baseActivity,
    burstLimit,
    ambience: value.ambience as AudienceMode['ambience']
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
    if (field in override && (!Number.isInteger(value) || typeof value !== 'number' || value < 0 || value > 4)) {
      issues.push(`${path}.${field} must be an integer from 0 to 4`)
    }
  }
  if ('cooldownMs' in override &&
    (!Number.isInteger(override.cooldownMs) || typeof override.cooldownMs !== 'number' || override.cooldownMs < 0)) {
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

function parseMeme(value: unknown, path: string, issues: string[]): MemeEntry | null {
  if (!isRecord(value)) return fail(path, 'must be an object', issues)
  const entry = value as unknown as MemeEntry
  const requiredNonEmptyStrings: Array<keyof MemeEntry> = [
    'id', 'modeId', 'text', 'normalizedText', 'familyKey', 'createdAt'
  ]
  for (const field of requiredNonEmptyStrings) {
    if (typeof entry[field] !== 'string' || !entry[field].trim()) {
      issues.push(`${path}.${field} must be a non-empty string`)
    }
  }
  if (typeof entry.id === 'string' && !STABLE_ID_PATTERN.test(entry.id)) {
    issues.push(`${path}.id must be a stable lowercase identifier`)
  }
  if (
    typeof entry.text === 'string' &&
    typeof entry.normalizedText === 'string' &&
    entry.normalizedText !== normalizeMemeText(entry.text)
  ) {
    issues.push(`${path}.normalizedText does not match text`)
  }
  stringArray(entry.personaTags, `${path}.personaTags`, issues)
  const sourceKinds = stringArray(entry.sourceKinds, `${path}.sourceKinds`, issues)
  const validSourceKinds = ['user_text', 'user_speech', 'screen_event', 'audience_barrage', 'manual']
  if (sourceKinds.length === 0 || sourceKinds.some((kind) => !validSourceKinds.includes(kind))) {
    issues.push(`${path}.sourceKinds contains an invalid source`)
  }
  if (typeof entry.evidenceSummary !== 'string') {
    issues.push(`${path}.evidenceSummary must be a string`)
  } else if (entry.evidenceSummary.length > 160) {
    issues.push(`${path}.evidenceSummary is too long`)
  }
  if (!['automatic', 'manual'].includes(entry.source)) issues.push(`${path}.source is invalid`)
  if (!['director', 'user'].includes(entry.createdBy)) issues.push(`${path}.createdBy is invalid`)
  if (!['active', 'inactive', 'archived'].includes(entry.status)) issues.push(`${path}.status is invalid`)
  if (typeof entry.createdAt !== 'string' || !Number.isFinite(Date.parse(entry.createdAt))) {
    issues.push(`${path}.createdAt is invalid`)
  }
  if (!Number.isInteger(entry.revision) || entry.revision < 1) issues.push(`${path}.revision is invalid`)
  if (!Number.isInteger(entry.usageCount) || entry.usageCount < 0) issues.push(`${path}.usageCount is invalid`)
  if (
    entry.lastUsedAt !== null &&
    (typeof entry.lastUsedAt !== 'string' || !Number.isFinite(Date.parse(entry.lastUsedAt)))
  ) {
    issues.push(`${path}.lastUsedAt is invalid`)
  }
  if (typeof entry.pinned !== 'boolean') issues.push(`${path}.pinned must be boolean`)
  if (issues.some((issue) => issue.startsWith(path))) return null
  return { ...entry, personaTags: [...entry.personaTags], sourceKinds: [...entry.sourceKinds] }
}

function clonePersonaOverride(override: Record<string, unknown>): PersonaOverride {
  const clone = { ...override }
  for (const field of ['traits', 'triggerPreferences', 'avoidPatterns', 'contentFlags'] as const) {
    if (Array.isArray(clone[field])) clone[field] = [...clone[field]]
  }
  return clone as PersonaOverride
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

function numberRange(value: unknown, path: string, issues: string[]): readonly [number, number] {
  if (!Array.isArray(value) || value.length !== 2 || value.some((item) => !Number.isInteger(item)) ||
    value[0] < 0 || value[1] < value[0]) {
    issues.push(`${path} must be an ascending non-negative integer pair`)
    return [0, 0]
  }
  return [value[0] as number, value[1] as number]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fail<T>(path: string, message: string, issues: string[]): T | null {
  issues.push(`${path} ${message}`)
  return null
}
