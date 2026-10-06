import { test, expect } from 'claude-code/testing'

test('session.measure writes usage.json for the goat', async ($, on) => {
  const writes: { path: string; text: string }[] = []
  on('env.get', ($, e) => ({ value: e.name === 'USERPROFILE' ? 'C:\\Users\\me' : undefined }))
  on('fs.read', () => ({ deny: 'ENOENT' }))
  on('fs.write', ($, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('clock.now', () => ({ value: 1_791_000_000_000 }))
  on('session.measure', ($, e) => ({ changed: e.changed }))
  await $.session.measure({
    context: { window: 200000, tokens: 68000, percent: 34, isAutoCompactEnabled: true, apiUsage: null },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-03T13:00:00Z' },
      { kind: 'seven_day', percentUsed: 41.2, resetsAt: '2026-10-07T09:00:00Z' },
    ],
    changed: ['rateLimits'],
  })
  expect(writes.length).toBe(1)
  expect(writes[0].path.endsWith('C:/Users/me/.claude/goat/usage.json')).toBe(true)
  const u = JSON.parse(writes[0].text)
  expect(u.context_pct).toBe(34)
  expect(u.rate_limits.five_hour.used_percentage).toBe(23.5)
  expect(u.rate_limits.seven_day.resets_at).toBe(Date.parse('2026-10-07T09:00:00Z') / 1000)
})
