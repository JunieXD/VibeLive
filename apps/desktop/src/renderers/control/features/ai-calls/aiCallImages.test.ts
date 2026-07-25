import { describe, expect, it } from 'vitest'
import { collectAiCallImageReferences } from './aiCallImages'

describe('AI call image references', () => {
  it('collects unique preview references from a nested request preview', () => {
    expect(collectAiCallImageReferences({
      input: [
        { type: 'media_ref', preview_id: 'image-1', mime_type: 'image/png', sha256: 'a' },
        {
          type: 'text_ref',
          value: { image: { type: 'media_ref', preview_id: 'image-2' } }
        },
        { type: 'media_ref', preview_id: 'image-1', mime_type: 'image/png', sha256: 'a' },
        { type: 'media_ref', mime_type: 'image/jpeg', sha256: 'legacy' }
      ]
    })).toEqual([
      { previewId: 'image-1', mimeType: 'image/png', sha256: 'a' },
      { previewId: 'image-2', mimeType: null, sha256: null },
      { previewId: null, mimeType: 'image/jpeg', sha256: 'legacy' }
    ])
  })
})
