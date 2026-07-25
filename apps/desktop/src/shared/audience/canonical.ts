import {
  PERSONA_DOCUMENT_VERSION,
  type Persona,
  type PersonaContent,
  type PersonaTemplate
} from './types'

export type PersonaTemplateInput = PersonaContent & {
  readonly documentVersion?: typeof PERSONA_DOCUMENT_VERSION
  readonly revision?: number
  readonly contentHash?: string
}

export function canonicalPersonaContent(persona: PersonaContent): string {
  return `${JSON.stringify({
    persona_id: persona.id,
    display_name: persona.name,
    initials: persona.initials,
    role: persona.role,
    color: persona.color,
    traits: [...persona.traits],
    speech_style: persona.speechStyle,
    behavior: normalizeLf(persona.behavior).trim(),
    trigger_preferences: [...persona.triggerPreferences],
    avoid_patterns: [...persona.avoidPatterns],
    silence_bias: persona.silenceBias,
    burst_bias: persona.burstBias,
    repetition_bias: persona.repetitionBias,
    cooldown_ms: persona.cooldownMs,
    max_comments_per_decision: persona.maxCommentsPerDecision,
    content_flags: [...persona.contentFlags],
    enabled: persona.enabled
  })}\n`
}

export function computePersonaContentHash(persona: PersonaContent): string {
  return `sha256:${sha256(canonicalPersonaContent(persona))}`
}

export function createPersonaTemplate(input: PersonaTemplateInput): PersonaTemplate {
  const content = clonePersonaContent(input)
  return {
    documentVersion: PERSONA_DOCUMENT_VERSION,
    revision: input.revision ?? 1,
    contentHash: computePersonaContentHash(content),
    ...content
  }
}

export function materializePersonaTemplate(
  persona: Persona,
  patch: Partial<Omit<PersonaContent, 'id'>> = {}
): PersonaTemplate {
  return createPersonaTemplate({
    ...persona,
    ...patch,
    id: persona.id,
    revision: 'revision' in persona ? persona.revision : 1
  })
}

export function revisePersonaTemplate(
  current: PersonaTemplate,
  patch: Partial<Omit<PersonaContent, 'id'>>
): PersonaTemplate {
  const content = clonePersonaContent({ ...current, ...patch })
  const contentHash = computePersonaContentHash(content)
  if (contentHash === current.contentHash) return current
  return {
    documentVersion: PERSONA_DOCUMENT_VERSION,
    revision: current.revision + 1,
    contentHash,
    ...content
  }
}

export function clonePersonaTemplate(persona: PersonaTemplate): PersonaTemplate {
  return {
    documentVersion: persona.documentVersion,
    revision: persona.revision,
    contentHash: persona.contentHash,
    ...clonePersonaContent(persona)
  }
}

function clonePersonaContent(persona: PersonaContent): PersonaContent {
  return {
    id: persona.id,
    name: persona.name,
    initials: persona.initials,
    role: persona.role,
    color: persona.color,
    traits: [...persona.traits],
    speechStyle: persona.speechStyle,
    behavior: normalizeLf(persona.behavior).trim(),
    triggerPreferences: [...persona.triggerPreferences],
    avoidPatterns: [...persona.avoidPatterns],
    silenceBias: persona.silenceBias,
    burstBias: persona.burstBias,
    repetitionBias: persona.repetitionBias,
    cooldownMs: persona.cooldownMs,
    maxCommentsPerDecision: persona.maxCommentsPerDecision,
    contentFlags: [...persona.contentFlags],
    enabled: persona.enabled
  }
}

function normalizeLf(value: string): string {
  return value.replace(/\r\n?/g, '\n')
}

function sha256(value: string): string {
  const constants = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ])
  const hash = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
  ])
  const bytes = new TextEncoder().encode(value)
  const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64
  const padded = new Uint8Array(paddedLength)
  padded.set(bytes)
  padded[bytes.length] = 0x80
  new DataView(padded.buffer).setUint32(paddedLength - 4, bytes.length * 8)
  const view = new DataView(padded.buffer)
  const schedule = new Uint32Array(64)
  const rotate = (word: number, amount: number): number =>
    (word >>> amount) | (word << (32 - amount))

  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      schedule[index] = view.getUint32(offset + index * 4)
    }
    for (let index = 16; index < 64; index += 1) {
      const first = schedule[index - 15]
      const second = schedule[index - 2]
      const sigma0 = rotate(first, 7) ^ rotate(first, 18) ^ (first >>> 3)
      const sigma1 = rotate(second, 17) ^ rotate(second, 19) ^ (second >>> 10)
      schedule[index] = schedule[index - 16] + sigma0 + schedule[index - 7] + sigma1
    }
    let [a, b, c, d, e, f, g, h] = hash
    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)
      const choice = (e & f) ^ (~e & g)
      const temporary1 = (h + sum1 + choice + constants[index] + schedule[index]) >>> 0
      const sum0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)
      const majority = (a & b) ^ (a & c) ^ (b & c)
      const temporary2 = (sum0 + majority) >>> 0
      h = g
      g = f
      f = e
      e = (d + temporary1) >>> 0
      d = c
      c = b
      b = a
      a = (temporary1 + temporary2) >>> 0
    }
    for (const [index, value] of [a, b, c, d, e, f, g, h].entries()) {
      hash[index] = hash[index] + value
    }
  }
  return [...hash].map((word) => word.toString(16).padStart(8, '0')).join('')
}
