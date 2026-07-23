import type { Persona } from './types'

export const PERSONA_DOCUMENT_VERSION = 1

type PersonaMetadata = Omit<Persona, 'behavior'> & {
  readonly version: typeof PERSONA_DOCUMENT_VERSION
}

export type PersonaValidationIssue = {
  readonly field: string
  readonly message: string
}

export type PersonaParseResult =
  | { readonly ok: true; readonly persona: Persona }
  | { readonly ok: false; readonly issues: readonly PersonaValidationIssue[] }

const ID_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/
const COLOR_PATTERN = /^#[0-9a-f]{6}$/i
const FIRST_FENCE_PATTERN = /^\s*```json[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*(?:\r?\n|$)([\s\S]*)$/i
const PERSONA_METADATA_FIELDS = [
  'version',
  'id',
  'name',
  'initials',
  'role',
  'color',
  'traits',
  'speechStyle',
  'triggerPreferences',
  'avoidPatterns',
  'silenceBias',
  'burstBias',
  'repetitionBias',
  'cooldownMs',
  'maxCommentsPerDecision',
  'contentFlags',
  'enabled'
] as const

export function validatePersona(persona: Persona): readonly PersonaValidationIssue[] {
  const issues: PersonaValidationIssue[] = []
  const requiredText: Array<[keyof Persona, unknown]> = [
    ['id', persona.id],
    ['name', persona.name],
    ['initials', persona.initials],
    ['role', persona.role],
    ['speechStyle', persona.speechStyle],
    ['behavior', persona.behavior]
  ]

  for (const [field, value] of requiredText) {
    if (typeof value !== 'string' || !value.trim()) {
      issues.push({ field, message: `${field} 不能为空` })
    }
  }
  if (typeof persona.id === 'string' && !ID_PATTERN.test(persona.id)) {
    issues.push({ field: 'id', message: 'id 必须是稳定的小写 snake_case 或 kebab-case 标识' })
  }
  if (typeof persona.color !== 'string' || !COLOR_PATTERN.test(persona.color)) {
    issues.push({ field: 'color', message: 'color 必须是 6 位十六进制颜色' })
  }
  if (typeof persona.initials === 'string' && persona.initials.length > 4) {
    issues.push({ field: 'initials', message: 'initials 最多 4 个字符' })
  }
  if (!Array.isArray(persona.traits) || persona.traits.length === 0) {
    issues.push({ field: 'traits', message: 'traits 至少包含一项' })
  } else if (persona.traits.some((trait) => typeof trait !== 'string' || !trait.trim())) {
    issues.push({ field: 'traits', message: 'traits 只能包含非空字符串' })
  }
  if (typeof persona.enabled !== 'boolean') {
    issues.push({ field: 'enabled', message: 'enabled 必须是布尔值' })
  }
  for (const field of ['silenceBias', 'burstBias', 'repetitionBias'] as const) {
    if (!Number.isInteger(persona[field]) || persona[field] < 0 || persona[field] > 4) {
      issues.push({ field, message: `${field} 必须是 0-4 的整数` })
    }
  }
  if (!Number.isInteger(persona.cooldownMs) || persona.cooldownMs < 0) {
    issues.push({ field: 'cooldownMs', message: 'cooldownMs 必须是非负整数' })
  }
  if (persona.maxCommentsPerDecision !== 1 && persona.maxCommentsPerDecision !== 2) {
    issues.push({ field: 'maxCommentsPerDecision', message: 'maxCommentsPerDecision 只能是 1 或 2' })
  }
  for (const field of ['triggerPreferences', 'avoidPatterns', 'contentFlags'] as const) {
    if (!Array.isArray(persona[field]) || persona[field].some((value) => typeof value !== 'string')) {
      issues.push({ field, message: `${field} 必须是字符串数组` })
    }
  }
  return issues
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function metadataToPersona(metadata: Record<string, unknown>, behavior: string): Persona {
  return {
    id: metadata.id as string,
    name: metadata.name as string,
    initials: metadata.initials as string,
    role: metadata.role as string,
    color: metadata.color as string,
    traits: metadata.traits as readonly string[],
    speechStyle: metadata.speechStyle as string,
    triggerPreferences: metadata.triggerPreferences as readonly string[],
    avoidPatterns: metadata.avoidPatterns as readonly string[],
    silenceBias: metadata.silenceBias as Persona['silenceBias'],
    burstBias: metadata.burstBias as Persona['burstBias'],
    repetitionBias: metadata.repetitionBias as Persona['repetitionBias'],
    cooldownMs: metadata.cooldownMs as number,
    maxCommentsPerDecision: metadata.maxCommentsPerDecision as Persona['maxCommentsPerDecision'],
    contentFlags: metadata.contentFlags as readonly string[],
    enabled: metadata.enabled as boolean,
    behavior
  }
}

export function parsePersonaMarkdown(markdown: string): PersonaParseResult {
  const match = markdown.match(FIRST_FENCE_PATTERN)
  if (!match) {
    return {
      ok: false,
      issues: [{ field: 'document', message: '文档必须以 fenced JSON 结构块开头' }]
    }
  }

  let metadata: unknown
  try {
    metadata = JSON.parse(match[1])
  } catch {
    return { ok: false, issues: [{ field: 'json', message: '结构块不是有效 JSON' }] }
  }
  if (!isRecord(metadata) || metadata.version !== PERSONA_DOCUMENT_VERSION) {
    return {
      ok: false,
      issues: [{
        field: 'version',
        message: `仅支持 personality.md 格式版本 ${PERSONA_DOCUMENT_VERSION}`
      }]
    }
  }
  const unknownFields = Object.keys(metadata).filter(
    (field) => !PERSONA_METADATA_FIELDS.includes(field as typeof PERSONA_METADATA_FIELDS[number])
  )
  const missingFields = PERSONA_METADATA_FIELDS.filter(
    (field) => !Object.hasOwn(metadata, field)
  )
  if (unknownFields.length > 0 || missingFields.length > 0) {
    return {
      ok: false,
      issues: [
        ...unknownFields.map((field) => ({
          field,
          message: `不支持 personality.md 字段 ${field}`
        })),
        ...missingFields.map((field) => ({
          field,
          message: `personality.md 缺少字段 ${field}`
        }))
      ]
    }
  }

  const persona = metadataToPersona(metadata, match[2].trim())
  const issues = validatePersona(persona)
  return issues.length > 0
    ? { ok: false, issues }
    : {
        ok: true,
        persona: {
          ...persona,
          traits: [...persona.traits],
          triggerPreferences: [...persona.triggerPreferences],
          avoidPatterns: [...persona.avoidPatterns],
          contentFlags: [...persona.contentFlags]
        }
      }
}

export function serializePersonaMarkdown(persona: Persona): string {
  const issues = validatePersona(persona)
  if (issues.length > 0) {
    throw new Error(issues.map((issue) => `${issue.field}: ${issue.message}`).join('; '))
  }
  const metadata: PersonaMetadata = {
    version: PERSONA_DOCUMENT_VERSION,
    id: persona.id,
    name: persona.name,
    initials: persona.initials,
    role: persona.role,
    color: persona.color,
    traits: persona.traits,
    speechStyle: persona.speechStyle,
    triggerPreferences: persona.triggerPreferences,
    avoidPatterns: persona.avoidPatterns,
    silenceBias: persona.silenceBias,
    burstBias: persona.burstBias,
    repetitionBias: persona.repetitionBias,
    cooldownMs: persona.cooldownMs,
    maxCommentsPerDecision: persona.maxCommentsPerDecision,
    contentFlags: persona.contentFlags,
    enabled: persona.enabled
  }
  return `\`\`\`json\n${JSON.stringify(metadata, null, 2)}\n\`\`\`\n\n${persona.behavior.trim()}\n`
}
