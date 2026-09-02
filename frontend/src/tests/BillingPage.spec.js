/**
 * Structural tests for the BillingPage on_probation handling.
 *
 * The page has three on_probation surfaces:
 *   1. A top-of-card <v-alert type="warning"> between the suspended
 *      and pool-exceeded alerts.
 *   2. The "Status" card chip color/icon/label switch to warning.
 *   3. The "Last Sync" status text reads "On probation" in warning
 *      color.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC = path.join(__dirname, '..', 'components', 'BillingPage.vue')
const src = fs.readFileSync(SFC, 'utf8')

// Computed bodies in this file use the block form
// (``const x = computed(() => { ... })``). Return the text from
// ``const NAME = computed`` up to the matching ``})``.
function bodyOf(name) {
  const m = src.match(
    new RegExp(`const ${name} = computed\\(\\(\\) => \\{[\\s\\S]*?\\n\\}\\)`)
  )
  expect(m, `${name} computed not found`).toBeTruthy()
  return m[0]
}

describe('BillingPage.vue — on_probation alert and status display', () => {
  it('onProbation computed checks last_heartbeat_status', () => {
    // Distinct from isSuspended — probation is a softer state than
    // payment_failed / account_suspended, and the alert UI differs.
    const fn = bodyOf('onProbation')
    expect(fn).toMatch(/last_heartbeat_status\s*===\s*'on_probation'/)
  })

  it('isSuspended does NOT include on_probation', () => {
    // If on_probation were in isSuspended, the user would see the
    // red "Account suspended" alert and a community-model downgrade
    // that doesn't match their actual state. Pin the separation.
    const body = bodyOf('isSuspended')
    expect(body).toMatch(/'account_suspended'/)
    expect(body).toMatch(/'payment_failed'/)
    expect(body).not.toMatch(/'on_probation'/)
  })

  it('warning alert renders for on_probation between suspended and pool-exceeded', () => {
    // The v-if chain is: isSuspended → onProbation → poolExceeded
    // → licenseExpiringSoon. Pin the order so the right alert wins
    // when multiple conditions could match.
    const suspended = src.indexOf('v-if="isSuspended"')
    const probation = src.indexOf('v-else-if="onProbation"')
    const pool = src.indexOf('v-else-if="poolExceeded"')
    expect(suspended).toBeGreaterThan(-1)
    expect(probation).toBeGreaterThan(suspended)
    expect(pool).toBeGreaterThan(probation)
    // The probation alert must be a warning (not error) and
    // use the credit-card icon as a visual cue.
    const block = src.match(/v-else-if="onProbation"[\s\S]*?<\/v-alert>/)
    expect(block, 'on_probation alert block not found').toBeTruthy()
    expect(block[0]).toMatch(/type="warning"/)
    expect(block[0]).toMatch(/mdi-credit-card-clock/)
    expect(block[0]).toMatch(/Your billing is on probation/)
    expect(block[0]).toMatch(/Update Payment Method/)
  })

  it('modeColor / modeIcon / modeLabel switch to warning on on_probation', () => {
    // The "Status" card chip must reflect on_probation even when
    // mode is still 'commercial' — otherwise the chip would say
    // "Commercial" in green while the alert above says billing is
    // in trouble. Inconsistency.
    const color = bodyOf('modeColor')
    const icon = bodyOf('modeIcon')
    const label = bodyOf('modeLabel')
    expect(color).toMatch(/'on_probation'[\s\S]{0,80}return 'warning'/)
    expect(icon).toMatch(/'on_probation'[\s\S]{0,80}mdi-shield-alert-outline/)
    expect(label).toMatch(/'on_probation'[\s\S]{0,80}'On Probation'/)
  })

  it('on_probation is checked before the m === "commercial" success branch in modeColor', () => {
    // Same rationale as the LicenseBadge test — without this
    // ordering the chip would stay green and the user would
    // miss the warning.
    const body = bodyOf('modeColor')
    const probationIdx = body.indexOf("'on_probation'")
    const commercialIdx = body.indexOf("'commercial'")
    expect(probationIdx).toBeGreaterThan(-1)
    expect(commercialIdx).toBeGreaterThan(-1)
    expect(probationIdx).toBeLessThan(commercialIdx)
  })

  it('Last Sync status text reads "On probation" in warning color', () => {
    // The Last Sync block on the card shows the user the raw
    // status from the cloud. Pin both the label and the color so
    // it doesn't silently fall through to the default grey
    // "on_probation" rendering.
    const label = bodyOf('heartbeatStatusLabel')
    expect(label).toMatch(/'on_probation'[\s\S]{0,80}'On probation'/)
    const color = bodyOf('heartbeatStatusColor')
    expect(color).toMatch(/'on_probation'[\s\S]{0,80}return 'warning'/)
  })
})

describe('BillingPage.vue — grace_period alert and end-date display', () => {
  // The cloud sends mode=commercial + status=grace_period when the
  // customer removed their card but still has paid time.  We render
  // a warning alert that names the exact date the service ends so
  // the user can plan.

  it('gracePeriod computed checks last_heartbeat_status', () => {
    const fn = bodyOf('gracePeriod')
    expect(fn).toMatch(/last_heartbeat_status\s*===\s*'grace_period'/)
  })

  it('gracePeriodEndLabel formats license_expires_at as a long date', () => {
    // The end date comes from the cloud's license_expires_at
    // field.  The label must use that field (not a hardcoded
    // date) so it stays in sync with the cloud.
    const fn = bodyOf('gracePeriodEndLabel')
    expect(fn).toMatch(/status\.value\?\.license_expires_at/)
    expect(fn).toMatch(/year: 'numeric'/)
    expect(fn).toMatch(/month: 'long'/)
    expect(fn).toMatch(/day: 'numeric'/)
    // Fallback for the case where the cloud didn't send the date.
    expect(fn).toMatch(/end of the current billing period/)
  })

  it('gracePeriodEndLabel prefers subscription_expires_at over license_expires_at', () => {
    // After the cloud-side refactor that auto-bumps/decrements
    // the pool, the cloud now sends two distinct dates:
    //   - ``license_expires_at`` (capped TTL of the license token)
    //   - ``subscription_expires_at`` (explicit
    //     ``camera_quota.grace_period_ends_at``)
    // The grace-period banner must prefer the explicit field so
    // the user sees the right date even when a later state (e.g.
    // trial) would otherwise win the cap.
    const fn = bodyOf('gracePeriodEndLabel')
    expect(fn).toMatch(/subscription_expires_at/)
    expect(fn).toMatch(
      /subscription_expires_at\s*\|\|[\s\S]{0,40}license_expires_at/
    )
  })

  it('warning alert renders for grace_period between suspended and on_probation', () => {
    // The v-if chain is: isSuspended → gracePeriod → onProbation →
    // poolExceeded → licenseExpiringSoon.  grace_period is checked
    // BEFORE on_probation because losing a card entirely is a more
    // serious state than a recent charge that failed — the banner
    // chain must show the more urgent state wins.
    const suspended = src.indexOf('v-if="isSuspended"')
    const grace = src.indexOf('v-else-if="gracePeriod"')
    const probation = src.indexOf('v-else-if="onProbation"')
    const pool = src.indexOf('v-else-if="poolExceeded"')
    expect(suspended).toBeGreaterThan(-1)
    expect(grace).toBeGreaterThan(suspended)
    expect(probation).toBeGreaterThan(grace)
    expect(pool).toBeGreaterThan(probation)

    // The grace-period alert is a warning, not an error — the
    // service is still active, but the user needs to act.
    const block = src.match(/v-else-if="gracePeriod"[\s\S]*?<\/v-alert>/)
    expect(block, 'grace_period alert block not found').toBeTruthy()
    expect(block[0]).toMatch(/type="warning"/)
    expect(block[0]).toMatch(/mdi-credit-card-off/)
    expect(block[0]).toMatch(/Your billing is in a grace period/)
    expect(block[0]).toMatch(/Add Payment Method/)
  })

  it('grace-period alert mentions the end date', () => {
    // The whole point of the grace_period UI is to tell the user
    // WHEN their service ends, not just that there's a problem.
    const block = src.match(/v-else-if="gracePeriod"[\s\S]*?<\/v-alert>/)
    expect(block, 'alert not found').toBeTruthy()
    expect(block[0]).toMatch(/gracePeriodEndLabel/)
    expect(block[0]).toMatch(/Add a new credit card/)
  })

  it('isSuspended does NOT include grace_period', () => {
    // grace_period is NOT suspension — the customer is still in
    // commercial mode.  Pin the separation so a refactor that
    // lumps them together is caught.
    const body = bodyOf('isSuspended')
    expect(body).toMatch(/'account_suspended'/)
    expect(body).toMatch(/'payment_failed'/)
    expect(body).not.toMatch(/'grace_period'/)
  })

  it('onProbation does NOT include grace_period', () => {
    // Distinct states: on_probation is a recent charge that
    // failed (card is on file), grace_period is the card removed
    // entirely.  They need different messages.
    const body = bodyOf('onProbation')
    expect(body).not.toMatch(/'grace_period'/)
  })
})

describe('BillingPage.vue — license-days text only shown when <= 7 days', () => {
  // The "Valid for N more days" / "X days remaining" / "Renews in
  // N day(s)" line under the Camera Pool card was previously shown
  // for ALL renewal windows — including "Valid for 14 more days"
  // for a fresh activation.  We hide the line when more than 7
  // days remain (matching the EXPIRING_SOON_DAYS constant) and
  // only surface the warning/error variants as the renewal date
  // approaches.  The user has plenty of time to act on a 30-day
  // license; the "renew soon" warning is the signal worth showing.

  // The two v-if spans (trial + commercial) both gate the entire
  // line on the days-remaining threshold.  Pin the conditions so
  // a refactor that drops the gate is caught by the test.

  it('trial_commercial span is gated on days <= 7', () => {
    // Match the trial span block — it must require
    // trialDaysRemaining <= 7 (or null check) in the v-if.
    // We match the span with the trial mode check and the
    // threshold guard.
    expect(src).toMatch(
      /v-if="status\.mode === 'trial_commercial'[\s\S]{0,200}<= 7"/
    )
  })

  it('commercial span is gated on days <= 7', () => {
    expect(src).toMatch(
      /v-else-if="status\.mode === 'commercial'[\s\S]{0,200}<= 7"/
    )
  })

  it('trial span does NOT show "X days remaining" for > 7 days', () => {
    // The "X days remaining" line was the long-form version that
    // made the badge noisy for fresh activations.  Pin its
    // absence so a refactor doesn't silently bring it back.
    expect(src).not.toMatch(/trialDaysRemaining > 7[\s\S]{0,200}days remaining/)
  })

  it('commercial span does NOT show "Valid for X more days" for > 7 days', () => {
    // The user's literal request: "get rid of the 'Valid for 14
    // more days' line".  Pin the absence of the long-form text
    // and the v-if that gated it.
    expect(src).not.toMatch(/Valid for {{ licenseDaysRemaining }} more days/)
    expect(src).not.toMatch(/licenseDaysRemaining > 7/)
  })

  it('warning + expired variants remain in the gated span', () => {
    // The 0 < days <= 7 "renew soon" warning and the <= 0
    // "expired" error must still be present inside the gated
    // span — only the > 7 case is hidden.
    const trialSpan = src.match(
      /<span v-if="status\.mode === 'trial_commercial'[\s\S]*?<\/span>/
    )
    expect(trialSpan, 'trial span not found').toBeTruthy()
    expect(trialSpan[0]).toMatch(/renew soon/)
    expect(trialSpan[0]).toMatch(/Expired/)

    const commercialSpan = src.match(
      /<span v-else-if="status\.mode === 'commercial'[\s\S]*?<\/span>/
    )
    expect(commercialSpan, 'commercial span not found').toBeTruthy()
    expect(commercialSpan[0]).toMatch(/Renews in/)
    expect(commercialSpan[0]).toMatch(/License expired/)
  })

  it('shows "Active" for commercial with > 7 days (the original bug)', () => {
    // Before the fix, a commercial license with > 7 days remaining
    // fell through to the "No active license" v-else fallback —
    // contradicting the "Commercial" chip at the top of the card.
    // The v-else-if branch for both commercial and trial_commercial
    // must render a positive "Active" indicator so the user
    // doesn't see "No active license" for a valid license.
    expect(src).toMatch(
      /v-else-if="status\.mode === 'commercial' \|\| status\.mode === 'trial_commercial'"/
    )
    // The Active branch must come BEFORE the "No active license"
    // v-else fallback, so it wins for valid licenses.
    const activeIdx = src.indexOf("status.mode === 'commercial' || status.mode === 'trial_commercial'")
    const fallbackIdx = src.indexOf('No active license')
    expect(activeIdx).toBeGreaterThan(-1)
    expect(fallbackIdx).toBeGreaterThan(-1)
    expect(activeIdx).toBeLessThan(fallbackIdx)
    // "Active" must be the label inside the branch.
    expect(src).toMatch(/<strong class="text-success">Active<\/strong>/)
  })

  it('"No active license" is reserved for community / no mode', () => {
    // Pin that the v-else "No active license" only fires for
    // community (or null mode) — not for valid commercial/trial
    // licenses, which the new "Active" branch handles.
    // We assert by source-text ordering: the "No active license"
    // span is the LAST v-else (no v-else-if after it).
    const noLicenseIdx = src.indexOf('No active license')
    const activeIdx = src.indexOf(
      "status.mode === 'commercial' || status.mode === 'trial_commercial'"
    )
    expect(noLicenseIdx).toBeGreaterThan(-1)
    expect(activeIdx).toBeLessThan(noLicenseIdx)
  })
})

describe('BillingPage.vue — onProbationEndLabel computed', () => {
  // Same rationale as gracePeriodEndLabel: the cloud now has a
  // probation window with an explicit end date.  The capped
  // license_expires_at also equals the probation end during
  // probation, so we accept either as the source of truth (and
  // prefer the explicit probation_ends_at if the cloud starts
  // sending it on the heartbeat).
  const fn = bodyOf('onProbationEndLabel')

  it('onProbationEndLabel computed exists', () => {
    expect(fn, 'computed not found').toBeTruthy()
  })

  it('uses probation_ends_at or license_expires_at as source', () => {
    expect(fn).toMatch(
      /probation_ends_at\s*\|\|[\s\S]{0,40}license_expires_at/
    )
  })

  it('formats the date as a long-form absolute date', () => {
    expect(fn).toMatch(/year: 'numeric'/)
    expect(fn).toMatch(/month: 'long'/)
    expect(fn).toMatch(/day: 'numeric'/)
  })

  it('falls back to a generic string if no date is set', () => {
    expect(fn).toMatch(/end of the current probation period/)
  })
})

describe('BillingPage.vue — on_probation alert shows probation end date', () => {
  // After the cloud-side refactor, the capped license_expires_at
  // is the probation end date during probation.  The alert must
  // surface it so the user knows when their commercial access ends.
  const block = src.match(/v-else-if="onProbation"[\s\S]*?<\/v-alert>/)

  it('alert body includes onProbationEndLabel', () => {
    // The body text is wrapped across multiple lines in the
    // source, so use [\s\S]*? between "commercial" and "access"
    // to tolerate a newline + indentation.
    expect(block, 'on_probation alert block not found').toBeTruthy()
    expect(block[0]).toMatch(/onProbationEndLabel/)
    expect(block[0]).toMatch(/commercial[\s\S]*?access until/i)
  })
})

describe('BillingPage.vue — pool-exceeded alert (now info tone, not warning)', () => {
  // After the cloud-side refactor that auto-bumps/decrements the
  // pool, the on-prem's pool-exceeded alert is informational, not
  // a problem the user must fix.

  it('pool-exceeded alert uses type="info" (not warning)', () => {
    const block = src.match(/v-else-if="poolExceeded"[\s\S]*?<\/v-alert>/)
    expect(block, 'pool-exceeded alert block not found').toBeTruthy()
    expect(block[0]).toMatch(/type="info"/)
    expect(block[0]).not.toMatch(/type="warning"/)
  })

  it('pool-exceeded alert uses an information icon (not alert-octagon)', () => {
    // The old mdi-alert-octagon-outline was the alarming "octagon
    // warning" icon.  We replaced it with the soft information
    // outline since the system handles the pool automatically now.
    const block = src.match(/v-else-if="poolExceeded"[\s\S]*?<\/v-alert>/)
    expect(block, 'alert not found').toBeTruthy()
    expect(block[0]).toMatch(/mdi-information-outline/)
    expect(block[0]).not.toMatch(/mdi-alert-octagon/)
  })

  it('pool-exceeded alert does NOT ask the user to "Increase the pool"', () => {
    const block = src.match(/v-else-if="poolExceeded"[\s\S]*?<\/v-alert>/)
    expect(block, 'alert not found').toBeTruthy()
    expect(block[0]).not.toMatch(/[Ii]ncrease the pool/)
  })

  it('pool-exceeded alert explains auto-adjust + card/credit notification', () => {
    // The body text is long enough that the editor wraps it across
    // multiple lines.  Use [\s\S]*? between "credit" and "card" so
    // the regex tolerates a newline + indentation in the middle of
    // the phrase.
    const block = src.match(/v-else-if="poolExceeded"[\s\S]*?<\/v-alert>/)
    expect(block, 'alert not found').toBeTruthy()
    expect(block[0]).toMatch(/automatically adjusted/i)
    expect(block[0]).toMatch(/credit[\s\S]*?card on file will be charged/i)
    expect(block[0]).toMatch(/statement credit/i)
  })

  it('pool-exceeded action button is "View Billing" (not "Manage Subscription")', () => {
    const block = src.match(/v-else-if="poolExceeded"[\s\S]*?<\/v-alert>/)
    expect(block, 'alert not found').toBeTruthy()
    expect(block[0]).toMatch(/View Billing/)
    expect(block[0]).not.toMatch(/Manage Subscription/)
  })
})

describe('BillingPage.vue — auto-trigger heartbeat on mount', () => {
  // The user navigates to /billing to see fresh state, so the page
  // fires a real cloud heartbeat (POST /api/billing/heartbeat/trigger)
  // on mount instead of waiting for the 60s polling loop. The polling
  // loop only does a local GET — without this auto-trigger, the page
  // could show a state up to 10 minutes stale (the heartbeat
  // scheduler's own interval).
  const triggerFn = src.match(
    /async function triggerHeartbeat\([\s\S]*?\n\}/
  )
  // The onMounted block has an inner arrow function (the subscribe
  // callback) which breaks naive non-greedy `[\s\S]*?\}\)` matches.
  // Match from the opening `onMounted(() => {` to the call to
  // `triggerHeartbeat({ silent: true })` so we capture the
  // auto-trigger call regardless of what else is in the body.
  const onMountedTriggerCall = src.match(
    /onMounted\(\(\) => \{[\s\S]*?triggerHeartbeat\(\{ silent: true \}\)/
  )

  it('triggerHeartbeat accepts a silent option', () => {
    // silent mode is what the auto-trigger uses — it suppresses the
    // success/error toasts that the user-initiated "Sync Now" button
    // would normally show.
    expect(triggerFn, 'triggerHeartbeat function not found').toBeTruthy()
    expect(triggerFn[0]).toMatch(
      /async function triggerHeartbeat\(\{ silent = false \} = \{\}\)/
    )
  })

  it('triggerHeartbeat skips when a heartbeat is already in flight', () => {
    // Without this guard, clicking "Sync Now" right after navigating
    // to the page (or remounting) would fire two parallel
    // heartbeats. The user-initiated case is also protected by the
    // button's :loading="heartbeating" but the auto-trigger has no
    // such UX-level guard.
    expect(triggerFn, 'triggerHeartbeat function not found').toBeTruthy()
    expect(triggerFn[0]).toMatch(
      /if \(heartbeating\.value\) return/
    )
  })

  it('silent mode does not set actionMessage on success', () => {
    // The auto-trigger fires on every page visit — flashing
    // "Synced successfully" every time would be noisy. The user
    // gets the fresh data via the re-render; they don't need a
    // toast to confirm it.
    expect(triggerFn, 'triggerHeartbeat function not found').toBeTruthy()
    expect(triggerFn[0]).toMatch(/if \(!silent\) actionMessage\.value = 'Synced successfully\.'/)
  })

  it('silent mode swallows errors (no actionError toast)', () => {
    // A 404 (unactivated) or 502 (portal unreachable) at navigation
    // time is a normal state, not something to alarm the user
    // about. The user-initiated "Sync Now" path still surfaces
    // these errors as red toasts — only the auto-trigger is silent.
    expect(triggerFn, 'triggerHeartbeat function not found').toBeTruthy()
    expect(triggerFn[0]).toMatch(/if \(silent\) return/)
  })

  it('onMounted calls triggerHeartbeat with silent: true', () => {
    // Pin the on-mount behavior so a refactor that removes the
    // auto-trigger (e.g. moving it back to a "refresh only if no
    // status" check) is caught by the test.
    expect(onMountedTriggerCall, 'onMounted -> triggerHeartbeat call not found').toBeTruthy()
  })

  it('onMounted always triggers (not only when status is missing)', () => {
    // The user comes here to see fresh data, not the cached
    // fallback. The 60s polling loop only does a local GET, so
    // even if `status` is set, the underlying state could be up to
    // 10 minutes stale. Pin that the auto-trigger is unconditional.
    expect(src).not.toMatch(
      /onMounted[\s\S]{0,500}if \(!billingStatus\.state\.status\)/
    )
  })
})
