import type { Register, EngineInterface, SessionRateLimit, SessionContextUsage } from 'claude-code'

// Writes ~/.claude/goat/usage.json (same format as goat_status.py) whenever
// Claude Code measures the session, so the goat's tooltip also works in the
// Claude desktop app, which does not run statusLine commands.

type Rate = Record<string, { used_percentage: number; resets_at?: number }>

async function save($: EngineInterface, limits: SessionRateLimit[], context?: SessionContextUsage) {
  const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME'))
  if (!home) return
  const file = `${home.replace(/\\/g, '/')}/.claude/goat/usage.json`
  let old: Record<string, unknown> = {}
  try {
    old = JSON.parse(await $.fs.read(file))
  } catch {}
  const now = (await $.clock.now()) / 1000
  const rate: Rate = {}
  for (const l of limits ?? []) {
    const t = l.resetsAt ? Date.parse(l.resetsAt) : NaN
    rate[l.kind] = { used_percentage: l.percentUsed, ...(isNaN(t) ? {} : { resets_at: t / 1000 }) }
  }
  const has = Object.keys(rate).length > 0
  const out = {
    ts: now,
    model: old.model ?? '',
    context_pct: context?.percent ?? old.context_pct ?? null,
    session: '',
    rate_limits: has ? rate : (old.rate_limits ?? null),
    rate_ts: has ? now : (old.rate_ts ?? null),
    source: 'mod',
  }
  await $.fs.write(file, JSON.stringify(out))
}

export const register: Register = (on) => {
  on('session.measure', async ($, e, next) => {
    try {
      await save($, e.rateLimits, e.context)
    } catch {}
    return next(e)
  })

  on('session.start', async ($, e, next) => {
    const r = await next(e)
    try {
      const u = await $.session.usage()
      await save($, u.rateLimits, u.context)
    } catch {}
    return r
  })
}
