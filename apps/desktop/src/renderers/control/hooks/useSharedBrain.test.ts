import { describe, expect, it } from 'vitest'
import { memoryResetRevision } from './useSharedBrain'

describe('Shared Brain memory reset revision', () => {
  it('uses the collection head rather than deriving a revision from memory items', () => {
    expect(memoryResetRevision({ room_id: 'room-1', revision: 12 })).toBe(12)
  })
})
