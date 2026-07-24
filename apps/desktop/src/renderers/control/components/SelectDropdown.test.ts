import { describe, expect, it } from 'vitest'
import {
  findNextEnabledOptionIndex,
  type SelectDropdownOption
} from './SelectDropdown'

const options: readonly SelectDropdownOption<string>[] = [
  { value: 'first', label: '第一项' },
  { value: 'disabled', label: '不可用', disabled: true },
  { value: 'last', label: '最后一项' }
]

describe('findNextEnabledOptionIndex', () => {
  it('skips disabled options in both directions', () => {
    expect(findNextEnabledOptionIndex(options, 0, 1)).toBe(2)
    expect(findNextEnabledOptionIndex(options, 2, -1)).toBe(0)
  })

  it('wraps around the option list', () => {
    expect(findNextEnabledOptionIndex(options, 2, 1)).toBe(0)
    expect(findNextEnabledOptionIndex(options, 0, -1)).toBe(2)
  })

  it('returns -1 when no option is available', () => {
    expect(
      findNextEnabledOptionIndex(
        [{ value: 'disabled', label: '不可用', disabled: true }],
        0,
        1
      )
    ).toBe(-1)
  })
})
