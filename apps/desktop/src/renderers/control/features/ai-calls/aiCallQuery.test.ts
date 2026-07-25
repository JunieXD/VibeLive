import { describe, expect, it } from 'vitest'
import { queryAllAiCalls } from './aiCallQuery'

describe('AI call pagination', () => {
  it('fails instead of looping on a repeated cursor', async () => {
    await expect(
      queryAllAiCalls({}, async () => ({
        items: [],
        next_cursor: 'same',
        metadata: {}
      }))
    ).rejects.toThrow('分页游标重复')
  })
})
