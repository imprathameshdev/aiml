/**
 * Structural tests for the dashboard billing banners.
 *
 * LiveDashboard renders three full-width banners above the camera
 * grid: grace-period, on-probation, pool-exceeded, and
 * community-mode. The banners use a custom <div> (not Vuetify's
 * <v-alert>) because v-alert rendered as display:grid and ended up
 * beside the camera grid rather than spanning full width.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC = path.join(__dirname, '..', 'components', 'LiveDashboard.vue')
const src = fs.readFileSync(SFC, 'utf8')

// Capture the entire on-probation banner block — from the v-if= on
// the outer <div> to the next <!-- comment (the start of the
// pool-exceeded banner). Using a lookahead avoids matching the wrong
// `</div>` (there are several nested ones in the body/actions divs).
const onProbationBanner = src.match(
  /v-if="canManageBilling && onProbation"[\s\S]*?(?=<!-- Pool-exceeded banner)/
)
const onProbationComputed = src.match(
  /const onProbation = computed\(\(\) =>[\s\S]*?\)/
)

// Same pattern for the pool-exceeded banner — end anchor is the
// next <!-- comment (the start of the community-mode banner).
const poolExceededBanner = src.match(
  /v-if="canManageBilling && poolExceeded && !poolBannerDismissed"[\s\S]*?(?=<!-- Community-mode upgrade banner)/
)
const poolBannerDismissedRef = src.match(
  /const poolBannerDismissed = ref\(false\)/
)

// And the new grace-period banner.
const gracePeriodBanner = src.match(
  /v-if="canManageBilling && gracePeriod"[\s\S]*?(?=<!-- Pool-exceeded banner)/
)
const gracePeriodComputed = src.match(
  /const gracePeriod = computed\(\(\) =>[\s\S]*?\)/
)
const gracePeriodEndLabelComputed = src.match(
  /const gracePeriodEndLabel = computed\(\(\) =>[\s\S]*?\n\}\)/
)

describe('LiveDashboard.vue — on_probation banner', () => {
  it('onProbation computed checks last_heartbeat_status', () => {
    // The banner must key off the cloud's last_heartbeat_status,
    // not the local mode (mode is still 'commercial' during
    // probation, so keying off mode would miss the warning).
    expect(onProbationComputed, 'onProbation computed not found').toBeTruthy()
    expect(onProbationComputed[0]).toMatch(
      /last_heartbeat_status\s*===\s*'on_probation'/
    )
  })

  it('on-probation banner uses warning variant and credit-card-clock icon', () => {
    // The banner is the user's main prompt to fix their card. The
    // mdi-credit-card-clock icon is the strongest visual cue that
    // this is a payment-method problem, not a generic alert.
    expect(onProbationBanner, 'on_probation banner block not found').toBeTruthy()
    expect(onProbationBanner[0]).toMatch(/community-banner--warning/)
    expect(onProbationBanner[0]).toMatch(/mdi-credit-card-clock/)
    expect(onProbationBanner[0]).toMatch(/On Probation/)
    expect(onProbationBanner[0]).toMatch(/Open Billing Portal/)
  })

  it('on-probation banner includes an action to open the billing portal', () => {
    // The user needs a one-click path to the billing portal from
    // the dashboard. The link target is status.billing_portal_url
    // (not a hardcoded URL) so it works across environments.
    expect(onProbationBanner, 'banner not found').toBeTruthy()
    expect(onProbationBanner[0]).toMatch(/:href="billingStatus\.billing_portal_url"/)
    expect(onProbationBanner[0]).toMatch(/target="_blank"/)
  })

  it('on-probation banner is gated on canManageBilling', () => {
    // Only users with manage_billing permission see the banner —
    // non-billing users have no action they can take.
    expect(src).toMatch(/v-if="canManageBilling && onProbation"/)
  })

  it('on-probation banner mentions updating credit card in body text', () => {
    expect(onProbationBanner, 'banner not found').toBeTruthy()
    expect(onProbationBanner[0]).toMatch(/payment didn't go through/i)
    expect(onProbationBanner[0]).toMatch(/Update your credit card/i)
  })
})

describe('LiveDashboard.vue — grace_period banner', () => {
  // The cloud sends mode=commercial + status=grace_period when the
  // customer removed their card but still has paid time.  The user
  // needs to know exactly when their service ends so they can decide
  // whether to add a new card in time.

  it('gracePeriod computed checks last_heartbeat_status', () => {
    expect(gracePeriodComputed, 'gracePeriod computed not found').toBeTruthy()
    expect(gracePeriodComputed[0]).toMatch(
      /last_heartbeat_status\s*===\s*'grace_period'/
    )
  })

  it('gracePeriodEndLabel computed formats license_expires_at', () => {
    // The end date comes from the cloud's license_expires_at
    // field.  The label must use that field (not a hardcoded
    // date) so it stays in sync with the cloud.
    expect(gracePeriodEndLabelComputed, 'computed not found').toBeTruthy()
    expect(gracePeriodEndLabelComputed[0]).toMatch(
      /billingStatus\.value\?\.license_expires_at/
    )
    // Long-form date so the user can read it at a glance.
    expect(gracePeriodEndLabelComputed[0]).toMatch(/year: 'numeric'/)
    expect(gracePeriodEndLabelComputed[0]).toMatch(/month: 'long'/)
    expect(gracePeriodEndLabelComputed[0]).toMatch(/day: 'numeric'/)
    // Fallback for the case where the cloud didn't send the date.
    expect(gracePeriodEndLabelComputed[0]).toMatch(
      /end of the current billing period/
    )
  })

  it('grace-period banner uses warning variant and credit-card-off icon', () => {
    // The user removed their card entirely, not just had a recent
    // charge fail.  The credit-card-off icon is the strongest
    // visual cue that this is a "card missing" problem.
    expect(gracePeriodBanner, 'banner not found').toBeTruthy()
    expect(gracePeriodBanner[0]).toMatch(/community-banner--warning/)
    expect(gracePeriodBanner[0]).toMatch(/mdi-credit-card-off/)
    expect(gracePeriodBanner[0]).toMatch(/Grace Period/)
    expect(gracePeriodBanner[0]).toMatch(/Add Payment Method/)
  })

  it('grace-period banner shows the end date in the body text', () => {
    // The body must include the end date — the whole point of the
    // banner is to tell the user WHEN their service ends, not just
    // that there's a problem.
    expect(gracePeriodBanner, 'banner not found').toBeTruthy()
    expect(gracePeriodBanner[0]).toMatch(/gracePeriodEndLabel/)
    expect(gracePeriodBanner[0]).toMatch(/Add a new credit card/)
  })

  it('grace-period banner is ordered BEFORE on-probation and pool-exceeded', () => {
    // grace_period is more urgent than on_probation (no card at
    // all vs card but recent charge failed).  Pin the file order
    // so the visual stack — first banner wins — has grace_period
    // at the top.
    const graceIdx = src.indexOf('v-if="canManageBilling && gracePeriod"')
    const onProbIdx = src.indexOf('v-if="canManageBilling && onProbation"')
    const poolIdx = src.indexOf('v-if="canManageBilling && poolExceeded')
    expect(graceIdx).toBeGreaterThan(-1)
    expect(onProbIdx).toBeGreaterThan(graceIdx)
    expect(poolIdx).toBeGreaterThan(onProbIdx)
  })

  it('on-probation banner shows the probation end date', () => {
    // The cloud caps license_expires_at at the earliest of
    // trial/grace/probation, so during probation the capped value
    // IS the probation end date.  The banner must surface it so
    // the user knows when their commercial access ends.
    // The body text is wrapped across multiple lines in the
    // source, so use [\s\S]*? between "commercial" and "access"
    // to tolerate a newline + indentation in the middle of the
    // phrase.
    expect(onProbationBanner, 'banner not found').toBeTruthy()
    expect(onProbationBanner[0]).toMatch(/onProbationEndLabel/)
    expect(onProbationBanner[0]).toMatch(
      /commercial[\s\S]*?access until/i
    )
  })
})

describe('LiveDashboard.vue — grace_period end date prefers subscription_expires_at', () => {
  // After the cloud-side refactor that auto-bumps/decrements the
  // pool, the cloud now sends two distinct dates:
  //   - ``license_expires_at`` (capped TTL of the license token)
  //   - ``subscription_expires_at`` (explicit
  //     ``camera_quota.grace_period_ends_at``)
  // The grace-period banner must prefer the explicit field so the
  // user sees the right date even when a later state (e.g. trial)
  // would otherwise win the cap.
  const gracePeriodEndLabelComputed = src.match(
    /const gracePeriodEndLabel = computed\(\(\) =>[\s\S]*?\n\}\)/
  )

  it('gracePeriodEndLabel computed exists', () => {
    expect(gracePeriodEndLabelComputed, 'computed not found').toBeTruthy()
  })

  it('prefers subscription_expires_at over license_expires_at', () => {
    // The check order matters: subscription_expires_at first,
    // license_expires_at as fallback.  Pin the order so the
    // explicit grace-period end wins.
    expect(gracePeriodEndLabelComputed, 'computed not found').toBeTruthy()
    expect(gracePeriodEndLabelComputed[0]).toMatch(
      /subscription_expires_at[\s\S]{0,40}\|/
    )
    // license_expires_at is the fallback — it must be in the
    // same expression as the OR.
    expect(gracePeriodEndLabelComputed[0]).toMatch(
      /subscription_expires_at\s*\|\|[\s\S]{0,40}license_expires_at/
    )
  })
})

describe('LiveDashboard.vue — onProbationEndLabel computed', () => {
  // Same rationale as gracePeriodEndLabel: the cloud now has a
  // probation window with an explicit end date.  The capped
  // license_expires_at also equals the probation end during
  // probation, so we accept either as the source of truth (and
  // prefer the explicit probation_ends_at if the cloud starts
  // sending it on the heartbeat).
  const onProbationEndLabelComputed = src.match(
    /const onProbationEndLabel = computed\(\(\) =>[\s\S]*?\n\}\)/
  )

  it('onProbationEndLabel computed exists', () => {
    expect(onProbationEndLabelComputed, 'computed not found').toBeTruthy()
  })

  it('uses probation_ends_at or license_expires_at as source', () => {
    expect(onProbationEndLabelComputed, 'computed not found').toBeTruthy()
    expect(onProbationEndLabelComputed[0]).toMatch(
      /probation_ends_at\s*\|\|[\s\S]{0,40}license_expires_at/
    )
  })

  it('formats the date as a long-form absolute date', () => {
    expect(onProbationEndLabelComputed, 'computed not found').toBeTruthy()
    expect(onProbationEndLabelComputed[0]).toMatch(/year: 'numeric'/)
    expect(onProbationEndLabelComputed[0]).toMatch(/month: 'long'/)
    expect(onProbationEndLabelComputed[0]).toMatch(/day: 'numeric'/)
  })

  it('falls back to a generic string if no date is set', () => {
    expect(onProbationEndLabelComputed, 'computed not found').toBeTruthy()
    expect(onProbationEndLabelComputed[0]).toMatch(
      /end of the current probation period/
    )
  })
})

describe('LiveDashboard.vue — pool-exceeded banner (now info tone, dismissible)', () => {
  // After the cloud-side refactor that auto-bumps/decrements the
  // pool, the on-prem's pool-exceeded banner is a notification
  // rather than a billing problem.  It should be info-toned,
  // dismissible, and not ask the user to "increase the pool size".

  it('uses community-banner--info (not --warning)', () => {
    // The banner is no longer an error/warning — the cloud handles
    // it.  Pin the variant class so a refactor doesn't silently
    // switch it back to the alarming red/orange tones.
    expect(poolExceededBanner, 'banner not found').toBeTruthy()
    expect(poolExceededBanner[0]).toMatch(/community-banner--info/)
    expect(poolExceededBanner[0]).not.toMatch(/community-banner--warning/)
  })

  it('uses Pool Adjusted chip and credit-card icon family', () => {
    // The chip name change signals to the user that this is a
    // notification, not an error.  The icon is a generic
    // information outline (no longer the alarming alert-octagon).
    expect(poolExceededBanner, 'banner not found').toBeTruthy()
    expect(poolExceededBanner[0]).toMatch(/Pool Adjusted/)
    expect(poolExceededBanner[0]).toMatch(/mdi-information-outline/)
    expect(poolExceededBanner[0]).not.toMatch(/Pool Exceeded/)
  })

  it('does NOT ask the user to "increase the pool size"', () => {
    // The whole point of the messaging refactor — the cloud handles
    // the pool size automatically now, so the on-prem must not
    // push the user to a "Increase the pool size" action that no
    // longer applies.
    expect(poolExceededBanner, 'banner not found').toBeTruthy()
    expect(poolExceededBanner[0]).not.toMatch(/[Ii]ncrease the pool size/)
  })

  it('explains the auto-bump + card/credit notification', () => {
    // The body should mention both directions of the auto-adjust
    // so the user knows what will happen on both add and remove.
    expect(poolExceededBanner, 'banner not found').toBeTruthy()
    expect(poolExceededBanner[0]).toMatch(/automatically adjusted/i)
    expect(poolExceededBanner[0]).toMatch(/credit card on file will be charged/i)
    expect(poolExceededBanner[0]).toMatch(/statement credit/i)
  })

  it('action button is "View Billing", not "Manage Subscription"', () => {
    // The previous "Manage Subscription" button implied the user
    // had to take action to fix the pool.  After the refactor the
    // user just wants to see their billing/invoices — the action
    // is informational, not remedial.
    expect(poolExceededBanner, 'banner not found').toBeTruthy()
    expect(poolExceededBanner[0]).toMatch(/View Billing/)
    expect(poolExceededBanner[0]).not.toMatch(/Manage Subscription/)
  })

  it('is dismissible via a close button', () => {
    // The banner is informational — the user shouldn't be forced
    // to look at it every time they open the dashboard.  The
    // close button binds to poolBannerDismissed.
    expect(poolExceededBanner, 'banner not found').toBeTruthy()
    expect(poolExceededBanner[0]).toMatch(
      /@click="poolBannerDismissed = true"/
    )
    expect(poolExceededBanner[0]).toMatch(/mdi-close/)
  })

  it('v-if includes the poolBannerDismissed guard', () => {
    // The banner should only show when poolExceeded is true AND
    // it hasn't been dismissed.  Pin the guard expression so a
    // refactor doesn't silently drop it.
    expect(src).toMatch(
      /v-if="canManageBilling && poolExceeded && !poolBannerDismissed"/
    )
  })

  it('has a poolBannerDismissed ref starting at false', () => {
    // The dismiss state needs a stable ref.  Pin the initial
    // value as false (banner starts visible) so a refactor can't
    // silently start the user with a dismissed banner.
    expect(poolBannerDismissedRef, 'poolBannerDismissed ref not found').toBeTruthy()
  })

  it('resets poolBannerDismissed when the pool state flips', () => {
    // Without this reset, a user who dismisses the banner would
    // never see it again — even if they later add another camera
    // that pushes the pool over the line.  The reset condition
    // lives in fetchBillingStatus.
    const reset = src.match(
      /if \(wasPoolExceeded !== poolExceeded\.value \|\| !poolExceeded\.value\)[\s\S]*?poolBannerDismissed\.value = false/
    )
    expect(reset, 'poolBannerDismissed reset condition not found').toBeTruthy()
  })
})

describe('LiveDashboard.vue — info banner CSS class exists', () => {
  // The pool-exceeded banner needs an info-tier visual.  The
  // existing CSS only had primary (default) and warning variants.
  // Pin that the info variant is defined so the banner's border
  // and background use the right color tokens.
  it('.community-banner--info class is defined', () => {
    expect(src).toMatch(/\.community-banner--info\s*\{/)
    // It should use the info theme color, not primary or warning.
    expect(src).toMatch(
      /\.community-banner--info\s*\{[\s\S]*?v-theme-info/
    )
  })
})
