import { describe, it, expect } from 'vitest'
import { formatSize, parseTimestamp } from '../utils/format'

describe('formatSize utility', () => {
  it('formats 0 bytes correctly', () => {
    expect(formatSize(0)).toBe('0 B')
  })

  it('formats KB correctly', () => {
    expect(formatSize(1024)).toBe('1 KB')
    expect(formatSize(1536)).toBe('1.5 KB')
  })

  it('formats MB correctly', () => {
    expect(formatSize(1024 * 1024)).toBe('1 MB')
    expect(formatSize(2.5 * 1024 * 1024)).toBe('2.5 MB')
  })

  it('formats GB correctly', () => {
    expect(formatSize(1024 * 1024 * 1024)).toBe('1 GB')
  })

  it('handles null, undefined, negative, and invalid inputs safely', () => {
    expect(formatSize(null)).toBe('0 B')
    expect(formatSize(undefined)).toBe('0 B')
    expect(formatSize(-100)).toBe('0 B')
    expect(formatSize(NaN)).toBe('0 B')
  })
})

describe('parseTimestamp utility', () => {
  it('handles naive ISO strings by assuming UTC', () => {
    const d = parseTimestamp('2026-08-27T14:44:20')
    expect(d).toBeInstanceOf(Date)
    expect(isNaN(d.getTime())).toBe(false)
    expect(d.toISOString()).toBe('2026-08-27T14:44:20.000Z')
  })

  it('handles explicit UTC strings with Z', () => {
    const d = parseTimestamp('2026-08-27T14:44:20Z')
    expect(d).toBeInstanceOf(Date)
    expect(isNaN(d.getTime())).toBe(false)
    expect(d.toISOString()).toBe('2026-08-27T14:44:20.000Z')
  })

  it('handles timestamps with negative timezone offsets without appending double Z', () => {
    const d = parseTimestamp('2026-08-27T14:44:20-05:00')
    expect(d).toBeInstanceOf(Date)
    expect(isNaN(d.getTime())).toBe(false)
    expect(d.toISOString()).toBe('2026-08-27T19:44:20.000Z')
  })

  it('handles timestamps with positive timezone offsets', () => {
    const d = parseTimestamp('2026-08-27T14:44:20+02:00')
    expect(d).toBeInstanceOf(Date)
    expect(isNaN(d.getTime())).toBe(false)
    expect(d.toISOString()).toBe('2026-08-27T12:44:20.000Z')
  })

  it('handles null, undefined, and Date objects safely', () => {
    expect(parseTimestamp(null)).toBeNull()
    expect(parseTimestamp(undefined)).toBeNull()
    const now = new Date()
    expect(parseTimestamp(now)).toBe(now)
  })
})


