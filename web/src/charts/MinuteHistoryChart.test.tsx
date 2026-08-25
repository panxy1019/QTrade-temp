import { describe, expect, it } from 'vitest'
import { formatChinaMinuteTimestamp } from './formatChinaMinuteTimestamp'

describe('minute history timestamps', () => {
  it('renders provider UTC timestamps in the A-share session timezone', () => {
    expect(formatChinaMinuteTimestamp('2025-01-02T01:35:00+00:00')).toBe('01-02 09:35')
    expect(formatChinaMinuteTimestamp('2025-01-02T07:00:00+00:00')).toBe('01-02 15:00')
  })
})
