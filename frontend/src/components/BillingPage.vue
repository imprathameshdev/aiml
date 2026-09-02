<template>
  <v-container>
    <v-card elevation="2" max-width="900" class="mx-auto">
      <v-toolbar color="primary" density="comfortable">
        <v-toolbar-title class="text-h6 text-white">
          License & Billing
        </v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn icon @click="refresh" :loading="loading" variant="text" color="white">
          <v-icon>mdi-refresh</v-icon>
        </v-btn>
      </v-toolbar>

      <v-card-text class="pa-6">
        <div v-if="!status">
          <v-progress-circular indeterminate color="primary"></v-progress-circular>
        </div>

        <div v-else-if="!status.billing_portal_url">
          <v-alert type="info" variant="tonal" class="mb-4">
            Billing portal is not configured on this install.
            Set <code>VULTURE_BILLING_PORTAL_URL</code> in the backend
            environment to enable commercial licenses.
          </v-alert>
          <v-card variant="outlined" class="pa-4">
            <div class="text-subtitle-1 font-weight-bold mb-2">Current mode</div>
            <v-chip color="grey-darken-1" prepend-icon="mdi-shield-off-outline">
              Community
            </v-chip>
            <div class="text-body-2 text-grey-darken-1 mt-3">
              Running on the bundled community model. Inference works but
              accuracy is reduced compared to the commercial model.
            </div>
          </v-card>
        </div>

        <div v-else>
          <v-alert
            v-if="!isDefaultServer"
            type="info"
            variant="tonal"
            density="comfortable"
            class="mb-4"
            prepend-icon="mdi-server"
          >
            Connected to custom license service: <strong>{{ configuredServerHost }}</strong>
          </v-alert>

          <v-alert
            v-if="isSuspended"
            type="error"
            variant="tonal"
            class="mb-4"
            prepend-icon="mdi-shield-alert"
          >
            <div class="font-weight-bold mb-1">Your account is suspended</div>
            <div>
              {{ suspensionMessage }}
              The on-prem engine has fallen back to the bundled community model.
              Contact billing support to restore service.
            </div>
          </v-alert>

          <v-alert
            v-else-if="gracePeriod"
            type="warning"
            variant="tonal"
            class="mb-4"
            prepend-icon="mdi-credit-card-off"
          >
            <div class="font-weight-bold mb-1">Your billing is in a grace period</div>
            <div class="mb-2">
              Your payment method was removed. Add a new credit card
              <strong>by {{ gracePeriodEndLabel }}</strong> to keep your
              commercial license. After that date, your service will
              end and the on-prem will fall back to the bundled
              community model.
            </div>
            <v-btn
              v-if="status.email"
              size="small"
              variant="tonal"
              color="warning"
              prepend-icon="mdi-credit-card-plus-outline"
              @click="manage"
            >
              Add Payment Method
            </v-btn>
          </v-alert>

          <v-alert
            v-else-if="onProbation"
            type="warning"
            variant="tonal"
            class="mb-4"
            prepend-icon="mdi-credit-card-clock"
          >
            <div class="font-weight-bold mb-1">Your billing is on probation</div>
            <div class="mb-2">
              Your last payment didn't go through. You have commercial
              access until <strong>{{ onProbationEndLabel }}</strong>.
              Update your payment method in the Billing Portal before
              that date to avoid losing access.
            </div>
            <v-btn
              v-if="status.email"
              size="small"
              variant="tonal"
              color="warning"
              prepend-icon="mdi-open-in-new"
              @click="manage"
            >
              Update Payment Method
            </v-btn>
          </v-alert>

          <v-alert
            v-else-if="poolExceeded"
            type="info"
            variant="tonal"
            class="mb-4"
            prepend-icon="mdi-information-outline"
          >
            <div class="font-weight-bold mb-1">
              Your camera count has changed
            </div>
            <div class="mb-2">
              You're using <strong>{{ totalUsed }}</strong> cameras across
              {{ status.device_count || 1 }} device{{ (status.device_count || 1) === 1 ? '' : 's' }}
              on a pool of <strong>{{ poolSize }}</strong>.
              Your pool is automatically adjusted to match — your credit
              card on file will be charged for additional cameras, and
              you'll receive a statement credit for any unused time when
              you remove cameras.
            </div>
            <v-btn
              v-if="status.email"
              size="small"
              variant="tonal"
              color="info"
              prepend-icon="mdi-open-in-new"
              @click="manage"
            >
              View Billing
            </v-btn>
          </v-alert>

          <v-alert
            v-else-if="licenseExpiringSoon"
            type="warning"
            variant="tonal"
            class="mb-4"
            prepend-icon="mdi-alert-clock"
          >
            <div class="font-weight-bold mb-1">
              Your {{ status.mode === 'trial_commercial' ? 'trial' : 'license' }} renews in {{ expiringSoonDays }} day{{ expiringSoonDays === 1 ? '' : 's' }}
            </div>
            <div>
              Add a payment method in the Billing Portal to avoid losing access when it expires.
            </div>
          </v-alert>

          <v-row class="mb-2">
            <v-col cols="12" md="6">
              <v-card variant="outlined" class="pa-4 h-100">
                <div class="text-overline text-grey-darken-1 mb-1">Status</div>
                <v-chip :color="modeColor" :prepend-icon="modeIcon" class="mb-2">
                  {{ modeLabel }}
                </v-chip>
                <div class="text-body-2" v-if="status.email">
                  Account: <strong>{{ status.email }}</strong>
                </div>
                <div class="text-body-2" v-else>
                  No account linked to this device.
                </div>
              </v-card>
            </v-col>

            <v-col cols="12" md="6">
              <v-card
                variant="outlined"
                class="pa-4 h-100"
                :class="{ 'pool-card-exceeded': poolExceeded }"
              >
                <div class="d-flex align-center justify-space-between mb-1">
                  <div class="text-overline text-grey-darken-1">Camera Pool</div>
                  <v-chip
                    v-if="poolExceeded"
                    size="x-small"
                    color="error"
                    variant="flat"
                    prepend-icon="mdi-alert-octagon"
                  >
                    Exceeded
                  </v-chip>
                </div>
                <div class="d-flex align-baseline ga-2 mb-2">
                  <div
                    class="text-h4 font-weight-black"
                    :class="poolExceeded ? 'text-error' : ''"
                  >
                    {{ totalUsed }}
                  </div>
                  <div class="text-h6 text-grey-darken-1 font-weight-regular">
                    / {{ status.quota || '∞' }} cameras
                  </div>
                </div>

                <!-- Visual breakdown: this device | others | overage -->
                <div v-if="status.quota > 0" class="mb-2">
                  <div class="pool-bar">
                    <div
                      class="pool-bar-segment pool-bar-segment-this"
                      :style="{ width: thisSharePercent + '%' }"
                    ></div>
                    <div
                      v-if="otherSharePercent > 0"
                      class="pool-bar-segment pool-bar-segment-other"
                      :style="{ width: otherSharePercent + '%' }"
                    ></div>
                    <div
                      v-if="poolExceeded"
                      class="pool-bar-segment pool-bar-segment-overage"
                      :style="{ width: overagePercent + '%' }"
                      :title="`Exceeds pool by ${overage} camera${overage === 1 ? '' : 's'}`"
                    ></div>
                  </div>
                  <div class="d-flex justify-space-between text-caption mt-1">
                    <span class="pool-legend pool-legend-this">
                      <span class="pool-dot pool-dot-this"></span>
                      This device
                      <strong class="ml-1">{{ status.this_device_cameras || 0 }}</strong>
                    </span>
                    <span
                      v-if="status.device_count > 1"
                      class="pool-legend pool-legend-other"
                    >
                      <span class="pool-dot pool-dot-other"></span>
                      {{ status.device_count - 1 }} other
                      <strong class="ml-1">{{ status.other_devices_cameras || 0 }}</strong>
                    </span>
                    <span
                      v-if="poolExceeded"
                      class="pool-legend pool-legend-overage"
                    >
                      <span class="pool-dot pool-dot-overage"></span>
                      +{{ overage }} over
                    </span>
                    <span
                      v-else-if="freeSharePercent > 0"
                      class="pool-legend pool-legend-free"
                    >
                      <span class="pool-dot pool-dot-free"></span>
                      {{ freeSharePercent }}% free
                    </span>
                  </div>
                </div>

                <div class="text-body-2 text-grey-darken-1 mt-2">
                  <span v-if="status.mode === 'trial_commercial' && trialDaysRemaining !== null && trialDaysRemaining <= 7">
                    Trial:
                    <strong v-if="trialDaysRemaining > 0" class="text-warning">
                      {{ trialDaysRemaining }} day{{ trialDaysRemaining === 1 ? '' : 's' }} remaining — renew soon
                    </strong>
                    <strong v-else class="text-error">Expired</strong>
                  </span>
                  <span v-else-if="status.mode === 'commercial' && licenseDaysRemaining !== null && licenseDaysRemaining <= 7">
                    <strong v-if="licenseDaysRemaining > 0" class="text-warning">
                      Renews in {{ licenseDaysRemaining }} day{{ licenseDaysRemaining === 1 ? '' : 's' }}
                    </strong>
                    <strong v-else class="text-error">License expired</strong>
                  </span>
                  <span v-else-if="status.mode === 'commercial' || status.mode === 'trial_commercial'">
                    <strong class="text-success">Active</strong>
                  </span>
                  <span v-else>No active license</span>
                </div>
              </v-card>
            </v-col>
          </v-row>

          <v-row class="mb-2">
            <v-col cols="12" md="6">
              <v-card variant="outlined" class="pa-4">
                <div class="text-overline text-grey-darken-1 mb-1">Hardware ID</div>
                <div class="text-caption font-weight-bold" style="font-family: monospace;">
                  {{ shortHwid }}
                </div>
                <div class="text-caption text-grey-darken-1 mt-1" style="font-family: monospace;">
                  Full ID stored locally only
                </div>
              </v-card>
            </v-col>

            <v-col cols="12" md="6">
              <v-card variant="outlined" class="pa-4">
                <div class="text-overline text-grey-darken-1 mb-1">Last Sync</div>
                <div class="text-body-2">
                  {{ formatDate(status.last_heartbeat_at) || 'Never' }}
                </div>
                <div class="text-caption text-grey-darken-1">
                  Status:
                  <strong :class="`text-${heartbeatStatusColor}`">
                    {{ heartbeatStatusLabel }}
                  </strong>
                  · {{ syncIntervalLabel }}
                </div>
              </v-card>
            </v-col>
          </v-row>

          <v-divider class="my-4"></v-divider>

          <div class="d-flex flex-wrap ga-2">
            <v-btn
              v-if="!status.email"
              color="primary"
              prepend-icon="mdi-link-variant"
              @click="connect"
              :loading="connecting"
            >
              Activate License
            </v-btn>

            <v-btn
              v-if="status.email"
              color="primary"
              variant="outlined"
              prepend-icon="mdi-open-in-new"
              @click="manage"
            >
              Manage Subscription
            </v-btn>

            <v-btn
              variant="outlined"
              prepend-icon="mdi-sync"
              @click="triggerHeartbeat"
              :loading="heartbeating"
            >
              Sync Now
            </v-btn>

            <v-btn
              v-if="status.email"
              variant="outlined"
              color="error"
              prepend-icon="mdi-link-variant-off"
              @click="disconnect"
              :loading="disconnecting"
            >
              Unlink &amp; Cancel
            </v-btn>
          </div>

          <v-alert v-if="actionMessage" type="success" variant="tonal" class="mt-4" closable @click:close="actionMessage = ''">
            {{ actionMessage }}
          </v-alert>
          <v-alert v-if="actionError" type="error" variant="tonal" class="mt-4" closable @click:close="actionError = ''">
            {{ actionError }}
          </v-alert>
        </div>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { billingApi } from '../api/billing'
import { billingStatus } from '../api/billingStatus'

const status = computed(() => billingStatus.state.status)
const loading = computed(() => billingStatus.state.loading)
const isDefaultServer = computed(() => {
  if (!status.value?.billing_portal_url) return true;
  try {
    const url = new URL(status.value.billing_portal_url);
    return url.hostname.toLowerCase() === 'license.lotvulture.com';
  } catch (e) {
    return status.value.billing_portal_url.toLowerCase().includes('license.lotvulture.com');
  }
});

const configuredServerHost = computed(() => {
  if (!status.value?.billing_portal_url) return '';
  try {
    const url = new URL(status.value.billing_portal_url);
    return url.origin;
  } catch (e) {
    return status.value.billing_portal_url;
  }
});
const connecting = ref(false)
const heartbeating = ref(false)
const disconnecting = ref(false)
const actionMessage = ref('')
const actionError = ref('')
let unsubscribe = null

const modeColor = computed(() => {
  const m = status.value?.mode
  // on_probation wins over the underlying mode — even if the cloud
  // still says mode=commercial, the status_code says "billing is in
  // trouble", and the chip should reflect that.
  if (status.value?.last_heartbeat_status === 'on_probation') return 'warning'
  if (m === 'commercial') return 'success'
  if (m === 'trial_commercial') return 'warning'
  const s = status.value?.last_heartbeat_status
  if (s === 'error' || s === 'auth_failed') return 'error'
  return 'grey-darken-1'
})

const modeIcon = computed(() => {
  const m = status.value?.mode
  if (status.value?.last_heartbeat_status === 'on_probation') return 'mdi-shield-alert-outline'
  if (m === 'commercial') return 'mdi-shield-check'
  if (m === 'trial_commercial') return 'mdi-shield-star-outline'
  const s = status.value?.last_heartbeat_status
  if (s === 'error') return 'mdi-shield-alert'
  return 'mdi-shield-off-outline'
})

const modeLabel = computed(() => {
  const m = status.value?.mode
  if (status.value?.last_heartbeat_status === 'on_probation') return 'On Probation'
  if (m === 'commercial') return 'Commercial'
  if (m === 'trial_commercial') return 'Trial'
  return 'Community License'
})

const EXPIRING_SOON_DAYS = 7

function daysUntil(iso) {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return 0
  return Math.ceil(ms / 86400000)
}

const trialDaysRemaining = computed(() => daysUntil(status.value?.trial_expires_at))
const licenseDaysRemaining = computed(() => daysUntil(status.value?.license_expires_at))

const expiringSoonDays = computed(() => {
  if (status.value?.mode === 'trial_commercial') return trialDaysRemaining.value
  if (status.value?.mode === 'commercial') return licenseDaysRemaining.value
  return null
})

const licenseExpiringSoon = computed(() => {
  const d = expiringSoonDays.value
  return d !== null && d > 0 && d <= EXPIRING_SOON_DAYS
})

const isSuspended = computed(() => {
  const s = status.value?.last_heartbeat_status
  return s === 'account_suspended' || s === 'payment_failed'
})

// on_probation is distinct from suspension: the customer is still
// in commercial mode, but billing is failing. We do NOT include it
// in isSuspended (the alert UX is different — warning, not error).
const onProbation = computed(() => {
  return status.value?.last_heartbeat_status === 'on_probation'
})

// grace_period is distinct from on_probation: the customer REMOVED
// their payment method entirely, not just had a recent charge fail.
// They still have paid time on their plan (mode=commercial) but the
// service ends at license_expires_at unless they add a new card.
const gracePeriod = computed(() => {
  return status.value?.last_heartbeat_status === 'grace_period'
})

// Human-readable end-of-grace-period date.  The cloud now sends
// two distinct dates: ``license_expires_at`` (the capped TTL of
// the license token) and ``subscription_expires_at`` (the explicit
// ``camera_quota.grace_period_ends_at``).  We prefer the explicit
// grace-period end so the banner shows the right date even when a
// later state (e.g. trial) would otherwise win the cap.
const gracePeriodEndLabel = computed(() => {
  const iso = status.value?.subscription_expires_at || status.value?.license_expires_at
  if (!iso) return 'the end of the current billing period'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'the end of the current billing period'
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
})

// Human-readable end-of-probation date.  The cloud caps
// ``license_expires_at`` at the earliest of trial/grace/probation/
// normal, so during probation the capped value is exactly the
// probation end date.  We also accept the explicit
// ``probation_ends_at`` field if the cloud starts sending it on
// the heartbeat in a future release (it's currently only on
// ``SessionResponse``).
const onProbationEndLabel = computed(() => {
  const iso = status.value?.probation_ends_at || status.value?.license_expires_at
  if (!iso) return 'the end of the current probation period'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'the end of the current probation period'
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
})

const suspensionMessage = computed(() => {
  const s = status.value?.last_heartbeat_status
  if (s === 'payment_failed') {
    return 'A recent payment failed — your subscription is past due.'
  }
  return 'Your account has been suspended by the billing administrator.'
})

function formatDate(iso) {
  if (!iso) return null
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

// Show only the first 12 hex chars of the HWID with an ellipsis. The
// full ID is sensitive enough that we don't want it splashed across the
// screen or copied into screenshots.
const shortHwid = computed(() => {
  const h = status.value?.hwid
  if (!h) return '—'
  if (h.length <= 12) return h
  return h.slice(0, 12) + '…'
})

// Camera pool breakdown computeds.
const thisCams = computed(() => Number(status.value?.this_device_cameras ?? 0))
const otherCams = computed(() => Number(status.value?.other_devices_cameras ?? 0))
const totalUsed = computed(() => thisCams.value + otherCams.value)
const poolSize = computed(() => Number(status.value?.quota ?? 0))

// Over-quota detection. We rely on the locally-known totals rather than
// the cloud's status_code so the UI updates immediately when the user
// enables/disables a camera — no need to wait for the next heartbeat.
const poolExceeded = computed(() => {
  const q = poolSize.value
  return q > 0 && totalUsed.value > q
})
const overage = computed(() => Math.max(0, totalUsed.value - poolSize.value))

// Customer-facing label for the sync interval. Raw backend field is
// `heartbeat_interval_seconds`; we render it as "every X min" (with
// fractional minutes shown to 1 dp).
const syncIntervalLabel = computed(() => {
  const secs = Number(status.value?.heartbeat_interval_seconds ?? 0)
  if (!secs || secs <= 0) return ''
  const mins = secs / 60
  const pretty = mins >= 1
    ? (Number.isInteger(mins) ? `${mins}` : mins.toFixed(1))
    : `<1`
  return `every ${pretty} min`
})

// Percentages for the stacked pool bar.
//
// Normal case: the bar shows this-device + other-devices segments
// filling a 100%-wide bar (where 100% == quota). The leftover
// "free" portion is the empty area to the right.
//
// Over-quota case: this + other segments still clip to 100%, and we
// draw a separate overage segment after them, sized relative to the
// quota so the user can see "we're 30% over our pool" at a glance.
const thisSharePercent = computed(() => {
  const q = poolSize.value
  if (q <= 0) return 0
  return Math.min(100, Math.round((thisCams.value / q) * 100))
})
const otherSharePercent = computed(() => {
  const q = poolSize.value
  if (q <= 0) return 0
  return Math.min(100 - thisSharePercent.value, Math.round((otherCams.value / q) * 100))
})
const freeSharePercent = computed(() => {
  const q = poolSize.value
  if (q <= 0) return 0
  return Math.max(0, 100 - thisSharePercent.value - otherSharePercent.value)
})
const overagePercent = computed(() => {
  const q = poolSize.value
  if (q <= 0 || !poolExceeded.value) return 0
  // Cap the visual overage at 100% so the bar stays sensible even when
  // the user is wildly over quota.
  return Math.min(100, Math.round((overage.value / q) * 100))
})

async function refresh() {
  await billingStatus.refresh()
}

async function connect() {
  connecting.value = true
  actionError.value = ''
  actionMessage.value = ''
  try {
    const r = await billingApi.connectUrl()
    window.open(r.url, '_blank')
    actionMessage.value = 'Opened billing portal. Complete activation there, then return to /billing/callback.'
  } catch (e) {
    actionError.value = `Could not build activation URL: ${e?.response?.data?.detail || e?.message || e}`
  } finally {
    connecting.value = false
  }
}

function manage() {
  if (status.value?.billing_portal_url) {
    window.open(status.value.billing_portal_url, '_blank')
  }
}

// Triggers a real cloud heartbeat. The `silent` option is used by the
// auto-trigger on onMounted so the page refreshes the cached status
// when the user navigates here, without flashing a "Synced
// successfully" toast on every page visit. Errors are also swallowed
// in silent mode — a navigation-time 404/502 (unactivated, portal
// unreachable) is a normal state, not something to alarm the user
// about. The user-initiated "Sync Now" button calls this without
// `silent` so it shows the toast + error path.
async function triggerHeartbeat({ silent = false } = {}) {
  // Skip if a heartbeat is already in flight — protects against
  // double-firing when the user clicks "Sync Now" right after
  // navigating to the page, and against concurrent re-mounts.
  if (heartbeating.value) return
  heartbeating.value = true
  if (!silent) {
    actionError.value = ''
    actionMessage.value = ''
  }
  try {
    const r = await billingApi.triggerHeartbeat()
    // Trigger endpoint returns the fresh status payload — push it into the
    // shared store so the LicenseBadge and any other subscribers update.
    billingStatus.state.status = r
    if (!silent) actionMessage.value = 'Synced successfully.'
  } catch (e) {
    if (silent) return
    const status = e?.response?.status
    const detail = e?.response?.data?.detail
    if (status === 404 && detail) {
      // Device hasn't been activated yet — show the message inline +
      // route the user to the activation flow.
      actionError.value = detail
    } else if (status === 502 && detail) {
      actionError.value = `Billing portal unreachable: ${detail}`
    } else if (status === 401) {
      actionError.value = 'Billing portal rejected this device. Try disconnecting and reconnecting.'
    } else if (status === 400 && detail) {
      actionError.value = detail
    } else {
      actionError.value = `Sync failed: ${detail || e?.message || e}`
    }
  } finally {
    heartbeating.value = false
  }
}

async function disconnect() {
  if (!confirm(
    'Unlink this device from billing and remove it from your account?\n\n' +
    'This will open the billing portal in a new window/tab to confirm and unlink this device from your account.'
  )) return
  disconnecting.value = true
  actionError.value = ''
  actionMessage.value = ''
  try {
    const res = await billingApi.disconnectUrl()
    if (res && res.url) {
      window.open(res.url, '_blank')
      actionMessage.value = 'Opened billing portal. Complete disconnection there, then return to /billing/callback.'
    } else {
      throw new Error('Failed to generate disconnect URL')
    }
  } catch (e) {
    const detail = e?.response?.data?.detail
    actionError.value = `Unlink failed: ${detail || e?.message || e}`
  } finally {
    disconnecting.value = false
  }
}

const heartbeatStatusLabel = computed(() => {
  if (!status.value) return 'Never'
  const s = status.value.last_heartbeat_status
  if (s === 'never') return 'Never'
  if (s === 'not_activated') return 'Device not activated'
  if (s === 'auth_failed') return 'Auth failed'
  if (s === 'error') return 'Unreachable'
  if (s === 'on_probation') return 'On probation'
  return s || 'Never'
})

const heartbeatStatusColor = computed(() => {
  if (!status.value) return 'grey'
  const s = status.value.last_heartbeat_status
  if (s === 'active' || s === 'never' || s === 'not_activated') return 'grey'
  if (s === 'on_probation') return 'warning'
  if (s === 'auth_failed' || s === 'error') return 'error'
  return 'grey'
})

onMounted(() => {
  billingStatus.startPolling()
  // Subscribe so this page re-renders when the shared store updates
  // (e.g. after the BillingCallbackPage writes a fresh status).
  unsubscribe = billingStatus.subscribe(() => {})
  // Always trigger a real cloud heartbeat on mount — the 60s polling
  // loop only does a local GET, so without this the page could show
  // a state that's up to 10 minutes stale (the heartbeat scheduler's
  // own interval). The user came here to see fresh data, so give it
  // to them. Silent mode avoids a "Synced successfully" toast on
  // every page visit; the trigger function also guards against
  // double-firing if the user clicks "Sync Now" right after
  // navigating here.
  triggerHeartbeat({ silent: true })
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
})
</script>

<style scoped>
.pool-bar {
  height: 10px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 100px;
  overflow: hidden;
  display: flex;
}

.pool-bar-segment {
  height: 100%;
  transition: width 0.3s ease;
}

.pool-bar-segment-this {
  background: rgb(var(--v-theme-primary));
}

.pool-bar-segment-other {
  background: rgba(var(--v-theme-primary), 0.4);
}

.pool-bar-segment-overage {
  background: rgb(var(--v-theme-error));
  /* Diagonal stripe pattern to make it unmistakable that we're past the limit */
  background-image: repeating-linear-gradient(
    45deg,
    rgba(255, 255, 255, 0.18) 0,
    rgba(255, 255, 255, 0.18) 4px,
    transparent 4px,
    transparent 8px
  );
}

/* When over quota, the whole card gets a red border + tinted background
   so it's impossible to miss on a busy dashboard. */
.pool-card-exceeded {
  border-color: rgb(var(--v-theme-error)) !important;
  background-color: rgba(var(--v-theme-error), 0.04);
}

.pool-legend {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.pool-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.pool-dot-this {
  background: rgb(var(--v-theme-primary));
}

.pool-dot-other {
  background: rgba(var(--v-theme-primary), 0.4);
}

.pool-dot-free {
  background: rgba(var(--v-theme-on-surface), 0.15);
}

.pool-dot-overage {
  background: rgb(var(--v-theme-error));
}

.pool-legend-overage {
  color: rgb(var(--v-theme-error));
  font-weight: 600;
}
</style>
