import {
  canonicalPersonaContent,
  computePersonaContentHash,
  createPersonaTemplate
} from './canonical'
import {
  PERSONA_DOCUMENT_VERSION,
  type Persona,
  type PersonaContent,
  type PersonaTemplate
} from './types'

export { PERSONA_DOCUMENT_VERSION } from './types'

export type PersonaValidationIssue = {
  readonly field: string
  readonly message: string
}

export type PersonaParseResult =
  | { readonly ok: true; readonly persona: PersonaTemplate }
  | { readonly ok: false; readonly issues: readonly PersonaValidationIssue[] }

const ID_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/
const COLOR_PATTERN = /^#[0-9a-f]{6}$/i
const HASH_PATTERN = /^sha256:[0-9a-f]{64}$/
const FIRST_FENCE_PATTERN = /^\s*```json[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*(?:\r?\n|$)([\s\S]*)$/i
const PERSONA_METADATA_FIELDS = [
  'document_version',
  'persona_id',
  'revision',
  'content_hash',
  'display_name',
  'initials',
  'role',
  'color',
  'traits',
  'speech_style',
  'trigger_preferences',
  'avoid_patterns',
  'silence_bias',
  'burst_bias',
  'repetition_bias',
  'cooldown_ms',
  'max_comments_per_decision',
  'content_flags',
  'enabled'
] as const

export function validatePersona(persona: Persona): readonly PersonaValidationIssue[]
export function validatePersona(persona: PersonaTemplate): readonly PersonaValidationIssue[]
export function validatePersona(
  persona: Persona | PersonaTemplate
): readonly PersonaValidationIssue[] {
  const issues = validatePersonaContent(persona)
  if (!('documentVersion' in persona)) return issues
  if (persona.documentVersion !== PERSONA_DOCUMENT_VERSION) {
    issues.push({ field: 'documentVersion', message: `documentVersion 必须是 ${PERSONA_DOCUMENT_VERSION}` })
  }
  if (!Number.isInteger(persona.revision) || persona.revision < 1) {
    issues.push({ field: 'revision', message: 'revision 必须是正整数' })
  }
  if (typeof persona.contentHash !== 'string' || !HASH_PATTERN.test(persona.contentHash)) {
    issues.push({ field: 'contentHash', message: 'contentHash 必须是 sha256 hash' })
  } else if (issues.length === 0 && persona.contentHash !== computePersonaContentHash(persona)) {
    issues.push({ field: 'contentHash', message: 'contentHash 与人格内容不匹配' })
  }
  return issues
}

function validatePersonaContent(persona: PersonaContent): PersonaValidationIssue[] {
  const issues: PersonaValidationIssue[] = []
  for (const [field, value] of [
    ['id', persona.id],
    ['name', persona.name],
    ['initials', persona.initials],
    ['role', persona.role],
    ['speechStyle', persona.speechStyle],
    ['behavior', persona.behavior]
  ] as const) {
    if (typeof value !== 'string' || !value.trim()) issues.push({ field, message: `${field} 不能为空` })
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
  if (!Array.isArray(persona.traits) || persona.traits.length === 0 ||
    persona.traits.some((trait) => typeof trait !== 'string' || !trait.trim())) {
    issues.push({ field: 'traits', message: 'traits 至少包含一个非空字符串' })
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

export function parsePersonaMarkdown(markdown: string): PersonaParseResult {
  const match = markdown.replace(/\r\n?/g, '\n').match(FIRST_FENCE_PATTERN)
  if (!match) {
    return { ok: false, issues: [{ field: 'document', message: '文档必须以 fenced JSON 结构块开头' }] }
  }

  let metadata: unknown
  try {
    metadata = JSON.parse(match[1])
  } catch {
    return { ok: false, issues: [{ field: 'json', message: '结构块不是有效 JSON' }] }
  }
  if (!isRecord(metadata) || metadata.document_version !== PERSONA_DOCUMENT_VERSION) {
    return {
      ok: false,
      issues: [{ field: 'document_version', message: `仅支持 personality.md 格式版本 ${PERSONA_DOCUMENT_VERSION}` }]
    }
  }
  const unknownFields = Object.keys(metadata).filter(
    (field) => !PERSONA_METADATA_FIELDS.includes(field as typeof PERSONA_METADATA_FIELDS[number])
  )
  const missingFields = PERSONA_METADATA_FIELDS.filter((field) => !Object.hasOwn(metadata, field))
  if (unknownFields.length > 0 || missingFields.length > 0) {
    return {
      ok: false,
      issues: [
        ...unknownFields.map((field) => ({ field, message: `不支持 personality.md 字段 ${field}` })),
        ...missingFields.map((field) => ({ field, message: `personality.md 缺少字段 ${field}` }))
      ]
    }
  }

  let persona: PersonaTemplate
  try {
    persona = createPersonaTemplate({
      id: metadata.persona_id as string,
      name: metadata.display_name as string,
      initials: metadata.initials as string,
      role: metadata.role as string,
      color: metadata.color as string,
      traits: metadata.traits as readonly string[],
      speechStyle: metadata.speech_style as string,
      behavior: match[2].trim(),
      triggerPreferences: metadata.trigger_preferences as readonly string[],
      avoidPatterns: metadata.avoid_patterns as readonly string[],
      silenceBias: metadata.silence_bias as PersonaTemplate['silenceBias'],
      burstBias: metadata.burst_bias as PersonaTemplate['burstBias'],
      repetitionBias: metadata.repetition_bias as PersonaTemplate['repetitionBias'],
      cooldownMs: metadata.cooldown_ms as number,
      maxCommentsPerDecision: metadata.max_comments_per_decision as PersonaTemplate['maxCommentsPerDecision'],
      contentFlags: metadata.content_flags as readonly string[],
      enabled: metadata.enabled as boolean,
      revision: metadata.revision as number
    })
  } catch {
    return { ok: false, issues: [{ field: 'document', message: 'personality.md 字段类型无效' }] }
  }
  const issues = [...validatePersona(persona)]
  if (metadata.content_hash !== persona.contentHash) {
    issues.push({ field: 'content_hash', message: 'content_hash 与 canonical 内容不匹配' })
  }
  return issues.length > 0 ? { ok: false, issues } : { ok: true, persona }
}

export function serializePersonaMarkdown(persona: Persona | PersonaTemplate): string {
  const template = 'documentVersion' in persona ? persona : createPersonaTemplate(persona)
  const issues = validatePersona(template)
  if (issues.length > 0) {
    throw new Error(issues.map((issue) => `${issue.field}: ${issue.message}`).join('; '))
  }
  const metadataContent = JSON.parse(canonicalPersonaContent(template)) as Record<string, unknown>
  delete metadataContent.behavior
  const metadata = {
    document_version: PERSONA_DOCUMENT_VERSION,
    persona_id: template.id,
    revision: template.revision,
    content_hash: template.contentHash,
    display_name: template.name,
    initials: template.initials,
    role: template.role,
    color: template.color,
    traits: [...template.traits],
    speech_style: template.speechStyle,
    trigger_preferences: [...template.triggerPreferences],
    avoid_patterns: [...template.avoidPatterns],
    silence_bias: template.silenceBias,
    burst_bias: template.burstBias,
    repetition_bias: template.repetitionBias,
    cooldown_ms: template.cooldownMs,
    max_comments_per_decision: template.maxCommentsPerDecision,
    content_flags: [...template.contentFlags],
    enabled: template.enabled,
    ...metadataContent
  }
  return `\`\`\`json\n${JSON.stringify(metadata, null, 2)}\n\`\`\`\n\n${template.behavior.trim()}\n`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
