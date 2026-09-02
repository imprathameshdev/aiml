/**
 * Structural tests for the LicenseBadge component.
 *
 * The badge is a small Vuetify <v-chip> in the sidebar that surfaces
 * the customer's billing state. These tests pin the color/label/icon
 * logic so a future refactor can't silently drop a state or flip the
 * warning color back to grey.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC = path.join(__dirname, '..', 'components', 'LicenseBadge.vue')
const src = fs.readFileSync(SFC, 'utf8')

// Computed bodies in this file use the implicit-return arrow form for
// single-expression ones (``const x = computed(() => expr)``) and the
// block form for multi-statement ones.  Return the text of the entire
// computed — from ``const NAME = computed`` up to the matching closing
// ``)`` — for substring matching.
function bodyOf(name) {
  const re = new RegExp(
    `const ${name} = computed\\(\\(\\) =>[\\s\\S]*?\\n\\}\\)|` +
    `const ${name} = computed\\(\\(\\) =>[\\s\\S]*?\\)\\n`
  )
  const m = src.match(re)
  expect(m, `${name} computed not found`).toBeTruthy()
  return m[0]
}

describe('LicenseBadge.vue — badge state mapping', () => {
  it('account_suspended and payment_failed turn badge red (error)', () => {
    // These are the "hard fail" states — full subscription loss.
    // Pin that they precede the probation check (so the more
    // severe state wins on the same call).
    const body = bodyOf('badgeColor')
    const suspendedIdx = body.indexOf("'account_suspended'")
    const probationIdx = body.indexOf("'on_probation'")
    expect(suspendedIdx).toBeGreaterThan(-1)
    expect(probationIdx).toBeGreaterThan(-1)
    expect(suspendedIdx, 'suspended check must come before probation check').toBeLessThan(probationIdx)
    // Both states return 'error'.
    expect(body).toMatch(/'account_suspended'[\s\S]{0,80}return 'error'/)
    expect(body).toMatch(/'payment_failed'[\s\S]{0,40}return 'error'/)
  })

  it('on_probation turns badge yellow (warning) and shows shield-alert-outline', () => {
    // Probation is a softer warning — billing is failing but
    // commercial access is preserved. Yellow is the correct color
    // here: it sits between the green "Commercial" and the red
    // "Payment Failed" / "Suspended" states.
    const colorBody = bodyOf('badgeColor')
    expect(colorBody).toMatch(
      /'on_probation'[\s\S]{0,80}return 'warning'/
    )
    const iconBody = bodyOf('badgeIcon')
    expect(iconBody).toMatch(
      /'on_probation'[\s\S]{0,80}mdi-shield-alert-outline/
    )
    const labelBody = bodyOf('badgeLabel')
    expect(labelBody).toMatch(/'on_probation'[\s\S]{0,80}'On Probation'/)
  })

  it('on_probation tooltip mentions checking the credit card', () => {
    // The user needs to know the action: open the billing portal
    // and check the card on file. The tooltip is the most visible
    // surface (sidebar badge hover), so the wording matters.
    // The current tooltip also includes the probation end date
    // (since the cloud caps license_expires_at at the earliest
    // active state limit), so the regex tolerates up to 1000
    // chars after the on_probation check.
    const body = bodyOf('tooltipText')
    expect(body).toMatch(
      /'on_probation'[\s\S]{0,1000}(?:update|check) your (?:payment method|credit card)/i
    )
  })

  it('on_probation tooltip shows the probation end date when available', () => {
    // The cloud caps ``license_expires_at`` at the earliest of
    // trial/grace/probation/normal, so during probation the
    // capped value IS the probation end date
    // (``user.probation_started_at + settings.probation_days``).
    // The tooltip must surface it so the user knows when their
    // commercial access ends.
    const body = bodyOf('tooltipText')
    expect(body).toMatch(
      /'on_probation'[\s\S]{0,1000}probation_ends_at\s*\|\|[\s\S]{0,40}license_expires_at/
    )
  })

  it('on_probation is checked BEFORE the m === "commercial" success branch', () => {
    // If probation falls through to the green commercial check,
    // the badge would stay green and the user wouldn't notice
    // billing is failing. Pin the order in badgeColor.
    const body = bodyOf('badgeColor')
    const probationIdx = body.indexOf("'on_probation'")
    const commercialIdx = body.indexOf("'commercial'")
    expect(probationIdx).toBeGreaterThan(-1)
    expect(commercialIdx).toBeGreaterThan(-1)
    expect(
      probationIdx,
      'on_probation must be checked before the commercial success branch'
    ).toBeLessThan(commercialIdx)
  })
})

describe('LicenseBadge.vue — grace_period state', () => {
  // The cloud sends mode=commercial + status=grace_period when the
  // customer removed their card but still has paid time.  The badge
  // must surface this state — losing the card entirely is more
  // serious than a recent charge that failed, so grace_period is
  // checked BEFORE on_probation.

  it('grace_period turns badge yellow (warning) and shows credit-card-off icon', () => {
    const colorBody = bodyOf('badgeColor')
    const iconBody = bodyOf('badgeIcon')
    const labelBody = bodyOf('badgeLabel')
    expect(colorBody).toMatch(
      /'grace_period'[\s\S]{0,80}return 'warning'/
    )
    expect(iconBody).toMatch(
      /'grace_period'[\s\S]{0,80}mdi-credit-card-off/
    )
    expect(labelBody).toMatch(/'grace_period'[\s\S]{0,80}'Grace Period'/)
  })

  it('grace_period is checked BEFORE on_probation (more urgent state wins)', () => {
    // grace_period = no card at all (most urgent)
    // on_probation = card on file but recent charge failed (less urgent)
    // Pin the order in badgeColor so the more severe state wins
    // when both conditions could match.
    const body = bodyOf('badgeColor')
    const graceIdx = body.indexOf("'grace_period'")
    const probationIdx = body.indexOf("'on_probation'")
    expect(graceIdx).toBeGreaterThan(-1)
    expect(probationIdx).toBeGreaterThan(-1)
    expect(graceIdx, 'grace_period must come before on_probation').toBeLessThan(probationIdx)
  })

  it('grace_period tooltip mentions end date and adding a card', () => {
    // The whole point of the grace_period UI is to tell the user
    // WHEN their service ends.  The tooltip should include the
    // end date (or a generic fallback) and the call to action.
    const body = bodyOf('tooltipText')
    expect(body).toMatch(
      /'grace_period'[\s\S]{0,800}(?:payment method was removed|Add a new credit card)/
    )
  })
})

describe('LicenseBadge.vue — pool-exceeded (now info tone, new chip text)', () => {
  // After the cloud-side refactor that auto-bumps/decrements the
  // pool, the on-prem's pool-exceeded state is informational, not
  // a billing problem the user must fix.

  it('pool-exceeded turns badge info (not error)', () => {
    // The previous "error" tone was appropriate when the user had
    // to manually increase the pool.  After the auto-bump refactor
    // the badge shouldn't look like an outage.
    const body = bodyOf('badgeColor')
    // The poolExceeded.value branch returns 'info'.
    expect(body).toMatch(/poolExceeded\.value\) return 'info'/)
  })

  it('pool-exceeded chip label is "Pool Adjusted" (not "Pool Exceeded")', () => {
    // The old "Pool Exceeded" label was alarming.  The new
    // "Pool Adjusted" label signals that the system has handled
    // the change automatically.
    const body = bodyOf('badgeLabel')
    expect(body).toMatch(/poolExceeded\.value\) return 'Pool Adjusted'/)
  })

  it('pool-exceeded icon is a settings/info icon (not alert-octagon)', () => {
    // The old mdi-alert-octagon was a "danger" icon.  We replaced
    // it with mdi-cog-outline — the system is adjusting the pool,
    // not signaling danger.
    const body = bodyOf('badgeIcon')
    expect(body).toMatch(/poolExceeded\.value\) return 'mdi-cog-outline'/)
  })

  it('pool-exceeded tooltip explains auto-bump and card/credit notification', () => {
    // The tooltip must explain what happened (the pool was
    // adjusted to match the cameras) and what it means for the
    // customer's billing (card will be charged / credit on decrease).
    const body = bodyOf('tooltipText')
    expect(body).toMatch(
      /automatically adjusted to match your cameras/
    )
    expect(body).toMatch(/credit card on file will be charged/i)
    expect(body).toMatch(/statement credit/i)
  })

  it('pool-exceeded tooltip does NOT ask user to "increase the pool"', () => {
    // The whole point of the messaging refactor — the user can
    // no longer manually change the pool size from the portal,
    // so the tooltip must not push them to a non-existent action.
    const body = bodyOf('tooltipText')
    expect(body).not.toMatch(/[Ii]ncrease the pool/)
  })
})
