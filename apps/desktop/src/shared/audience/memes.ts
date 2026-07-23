import type { MemeCandidate, MemeCreator, MemeEntry } from './types'

export type MemeIngestInput = MemeCandidate & {
  readonly createdBy?: MemeCreator
}

export type MemeIngestResult =
  | { readonly accepted: true; readonly entries: readonly MemeEntry[]; readonly entry: MemeEntry }
  | { readonly accepted: false; readonly entries: readonly MemeEntry[]; readonly reason: 'duplicate' | 'family-suppressed' }

export type MemeConflictReason = 'duplicate' | 'family-suppressed'

export type MemeConflictCandidate = Pick<
  MemeEntry,
  'id' | 'modeId' | 'normalizedText' | 'familyKey'
>

export type ArchiveStaleMemesOptions = {
  readonly staleAfterDays?: number
  readonly minimumUsageCount?: number
}

const DAY_MS = 24 * 60 * 60 * 1_000

export function normalizeMemeText(text: string): string {
  const normalized = text.normalize('NFKC').toLocaleLowerCase()
  const lexical = normalized.replace(/[\s\p{P}\p{S}]+/gu, '')
  return lexical || normalized.replace(/\s+/g, '')
}

export function findMemeConflict(
  entries: readonly MemeEntry[],
  candidate: MemeConflictCandidate
): MemeConflictReason | null {
  const comparable = entries.filter(
    (entry) =>
      entry.id !== candidate.id &&
      entry.modeId === candidate.modeId &&
      entry.status !== 'archived'
  )
  if (comparable.some((entry) => entry.normalizedText === candidate.normalizedText)) {
    return 'duplicate'
  }
  if (
    comparable.some(
      (entry) => entry.familyKey.toLocaleLowerCase() === candidate.familyKey.toLocaleLowerCase()
    )
  ) {
    return 'family-suppressed'
  }
  return null
}

export function autoIngestMeme(
  entries: readonly MemeEntry[],
  input: MemeIngestInput
): MemeIngestResult {
  const text = input.text.trim()
  if (!text) throw new Error('Meme text is required')
  if (!input.modeId.trim()) throw new Error('Meme modeId is required')
  if (input.sourceKinds.length === 0) throw new Error('Meme sourceKinds cannot be empty')
  if (input.evidenceSummary.length > 160) throw new Error('Meme evidenceSummary cannot exceed 160 characters')
  if (!Number.isFinite(Date.parse(input.createdAt))) throw new Error('Meme createdAt must be a valid date')
  if (entries.some((entry) => entry.id === input.id)) throw new Error(`Meme id already exists: ${input.id}`)

  const normalizedText = normalizeMemeText(text)
  const familyKey = (input.familyKey?.trim() || normalizedText).toLocaleLowerCase()
  const conflict = findMemeConflict(entries, {
    id: input.id,
    modeId: input.modeId,
    normalizedText,
    familyKey
  })
  if (conflict) {
    return { accepted: false, entries, reason: conflict }
  }

  const entry: MemeEntry = {
    id: input.id,
    modeId: input.modeId,
    text,
    normalizedText,
    familyKey,
    personaTags: [...(input.personaTags ?? [])],
    sourceKinds: [...input.sourceKinds],
    evidenceSummary: input.evidenceSummary.trim(),
    createdBy: input.createdBy ?? 'director',
    source: 'automatic',
    createdAt: input.createdAt,
    revision: 1,
    lastUsedAt: null,
    usageCount: 0,
    status: 'active',
    pinned: false
  }
  return { accepted: true, entries: [...entries, entry], entry }
}

export function undoAutomaticMeme(entries: readonly MemeEntry[], id: string): readonly MemeEntry[] {
  const target = requireMeme(entries, id)
  if (target.source !== 'automatic') throw new Error('Only automatically ingested memes can be undone')
  return entries.filter((entry) => entry.id !== id)
}

export function disableMeme(entries: readonly MemeEntry[], id: string): readonly MemeEntry[] {
  return updateMeme(entries, id, (entry) => revise(entry, { status: 'inactive' }))
}

export function setMemePinned(
  entries: readonly MemeEntry[],
  id: string,
  pinned: boolean
): readonly MemeEntry[] {
  return updateMeme(entries, id, (entry) => revise(entry, { pinned }))
}

export function archiveMeme(entries: readonly MemeEntry[], id: string): readonly MemeEntry[] {
  return updateMeme(entries, id, (entry) => revise(entry, { status: 'archived', pinned: false }))
}

export function restoreMeme(entries: readonly MemeEntry[], id: string): readonly MemeEntry[] {
  const target = requireMeme(entries, id)
  const conflict = findMemeConflict(entries, target)
  if (conflict === 'duplicate') {
    throw new Error(`Cannot restore duplicate meme: ${id}`)
  }
  if (conflict === 'family-suppressed') {
    throw new Error(`Cannot restore suppressed meme family: ${target.familyKey}`)
  }
  return updateMeme(entries, id, (entry) => revise(entry, { status: 'active' }))
}

export function recordMemeUsage(
  entries: readonly MemeEntry[],
  id: string,
  usedAt: string
): readonly MemeEntry[] {
  return updateMeme(entries, id, (entry) =>
    revise(entry, { lastUsedAt: usedAt, usageCount: entry.usageCount + 1 })
  )
}

export function archiveStaleMemes(
  entries: readonly MemeEntry[],
  asOf: string | Date,
  options: ArchiveStaleMemesOptions = {}
): readonly MemeEntry[] {
  const asOfMs = asOf instanceof Date ? asOf.getTime() : Date.parse(asOf)
  if (!Number.isFinite(asOfMs)) throw new Error('asOf must be a valid date')

  const staleAfterDays = options.staleAfterDays ?? 30
  const minimumUsageCount = options.minimumUsageCount ?? 3
  if (!Number.isFinite(staleAfterDays) || staleAfterDays < 0) {
    throw new Error('staleAfterDays must be non-negative')
  }
  if (!Number.isInteger(minimumUsageCount) || minimumUsageCount < 0) {
    throw new Error('minimumUsageCount must be a non-negative integer')
  }
  const staleAfterMs = staleAfterDays * DAY_MS

  return entries.map((entry) => {
    if (entry.pinned || entry.status === 'archived' || entry.usageCount >= minimumUsageCount) {
      return entry
    }
    const referenceMs = entry.lastUsedAt === null
      ? Date.parse(entry.createdAt)
      : Date.parse(entry.lastUsedAt)
    if (!Number.isFinite(referenceMs) || asOfMs - referenceMs <= staleAfterMs) return entry
    return revise(entry, { status: 'archived', pinned: false })
  })
}

function requireMeme(entries: readonly MemeEntry[], id: string): MemeEntry {
  const target = entries.find((entry) => entry.id === id)
  if (!target) throw new Error(`Unknown meme entry: ${id}`)
  return target
}

function updateMeme(
  entries: readonly MemeEntry[],
  id: string,
  update: (entry: MemeEntry) => MemeEntry
): readonly MemeEntry[] {
  requireMeme(entries, id)
  return entries.map((entry) => (entry.id === id ? update(entry) : entry))
}

function revise(entry: MemeEntry, change: Partial<MemeEntry>): MemeEntry {
  return { ...entry, ...change, revision: entry.revision + 1 }
}
