/**
 * Structural tests for AlertHistory.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC = path.join(__dirname, '..', 'components', 'AlertHistory.vue')
const SFC_ALERTS = path.join(__dirname, '..', 'components', 'Alerts.vue')
const src = fs.readFileSync(SFC, 'utf8')
const alertsSrc = fs.readFileSync(SFC_ALERTS, 'utf8')

describe('AlertHistory.vue (H4)', () => {
  it('fetches events from /api/alerts with status / rule_id / since params', () => {
    const fn = src.match(/const fetchEvents = async[\s\S]*?\n\};/)
    expect(fn, 'fetchEvents not found').toBeTruthy()
    expect(fn[0]).toMatch(/axios\.get\(['"]\/api\/alerts['"]/)
    // The buildParams helper composes the query string.
    expect(fn[0]).toMatch(/params:\s*buildParams\(\)/)
    expect(src).toMatch(/const buildParams = \(\) =>/)
  })

  it('exposes start/stop auto-refresh so the parent can pause it on tab change', () => {
    // We use defineExpose so the parent can pause polling when the
    // user navigates away from the History tab.
    const m = src.match(/defineExpose\(\s*\{[\s\S]*?\}\s*\)/)
    expect(m, 'defineExpose call not found').toBeTruthy()
    expect(m[0]).toMatch(/startAutoRefresh/)
    expect(m[0]).toMatch(/stopAutoRefresh/)
    expect(src).toMatch(/const startAutoRefresh = \(\) =>/)
    expect(src).toMatch(/const stopAutoRefresh = \(\) =>/)
  })

  it('has a default auto-refresh interval of 30 seconds', () => {
    // The plan calls for 30s auto-refresh; pin it.
    expect(src).toMatch(/autoRefreshSeconds:\s*\{\s*type:\s*Number,\s*default:\s*30\s*\}/)
  })

  it('retry button POSTs to /api/alerts/{id}/retry', () => {
    expect(src).toMatch(/axios\.post\(`\/api\/alerts\/\$\{item\.id\}\/retry`\)/)
  })

  it('retry button is hidden when the user lacks manage_alerts OR the budget is exhausted', () => {
    // canRetry gates on canManage prop + attempts < MAX.
    const fn = src.match(/const canRetry = \(item\) => \{[\s\S]*?\n\};/)
    expect(fn, 'canRetry not found').toBeTruthy()
    expect(fn[0]).toMatch(/props\.canManage/)
    expect(fn[0]).toMatch(/item\.attempts >= maxAttempts/)
  })

  it('retry button is hidden for sent events (only failed is retryable)', () => {
    // Regression: a successfully delivered event must NOT show a
    // retry button.  The old canRetry only blocked 'fired' and
    // exhausted attempts, so 'sent' events (which already worked)
    // also showed the button.  Re-firing a delivered alert sends
    // a duplicate to the same downstream channel.
    const fn = src.match(/const canRetry = \(item\) => \{[\s\S]*?\n\};/)
    expect(fn, 'canRetry not found').toBeTruthy()
    // Must explicitly require status === 'failed'.
    expect(fn[0]).toMatch(/item\.status\s*[!=]==\s*['"]failed['"]/)
    // Must NOT allow 'sent' through.
    expect(fn[0]).not.toMatch(/item\.status\s*!==\s*['"]fired['"]/)
  })

  it('renders status chips with the right colors', () => {
    // Status chip colors: sent=success, failed=error, fired=info.
    const fn = src.match(/const statusColor = \(s\) => \{[\s\S]*?\n\};/)
    expect(fn, 'statusColor not found').toBeTruthy()
    expect(fn[0]).toMatch(/'sent'.*'success'|"sent".*"success"/s)
    expect(fn[0]).toMatch(/'failed'.*'error'|"failed".*"error"/s)
    expect(fn[0]).toMatch(/'fired'.*'info'|"fired".*"info"/s)
  })

  it('expanded row shows the payload as a JSON <pre>', () => {
    expect(src).toMatch(/<template #expanded-row=/)
    expect(src).toMatch(/<pre[\s\S]*?\{\{ formatPayload\(item\.payload\) \}\}/)
  })
})

describe('Alerts.vue — History tab (H4)', () => {
  it('mounts AlertHistory and forwards the canManage prop', () => {
    expect(alertsSrc).toMatch(/<AlertHistory[^>]*ref="historyRef"/)
    expect(alertsSrc).toMatch(/:can-manage="canManage"/)
  })

  it('pauses auto-refresh when the user leaves the History tab', () => {
    expect(alertsSrc).toMatch(/historyRef\.value\.stopAutoRefresh/)
    expect(alertsSrc).toMatch(/historyRef\.value\.startAutoRefresh/)
  })
})
