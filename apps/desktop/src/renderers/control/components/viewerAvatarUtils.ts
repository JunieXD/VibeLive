export const VIEWER_AVATAR_VARIANT_COUNT = 8
export const VIEWER_AVATAR_TONE_COUNT = 8

function hashSeed(seed: string): number {
  let hash = 2_166_136_261

  for (const character of seed) {
    hash ^= character.codePointAt(0) ?? 0
    hash = Math.imul(hash, 16_777_619)
  }

  return hash >>> 0
}

function indexForSeed(seed: string, count: number): number {
  return hashSeed(seed.trim() || 'advx-viewer') % count
}

export function viewerAvatarVariant(seed: string): number {
  return indexForSeed(seed, VIEWER_AVATAR_VARIANT_COUNT)
}

export function viewerAvatarTone(seed: string): number {
  return indexForSeed(seed, VIEWER_AVATAR_TONE_COUNT)
}

export function leadingGrapheme(value: string): string {
  const normalized = value.trim()
  if (!normalized) return '?'

  const segmenter = new Intl.Segmenter('zh-CN', { granularity: 'grapheme' })
  for (const { segment } of segmenter.segment(normalized)) {
    return segment
  }

  return '?'
}
