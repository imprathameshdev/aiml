<template>
  <v-tooltip location="bottom">
    <template v-slot:activator="{ props }">
      <v-chip
        v-bind="props"
        :color="badgeColor"
        :prepend-icon="badgeIcon"
        size="small"
        class="font-weight-bold"
        variant="elevated"
        @click="goToBilling"
        style="cursor: pointer;"
      >
        {{ badgeLabel }}
      </v-chip>
    </template>
    <span>{{ tooltipText }}</span>
  </v-tooltip>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { billingStatus } from '../api/billingStatus'

const router = useRouter()

const EXPIRING_SOON_DAYS = 7

const status = computed(() => billingStatus.state.status)
const error = computed(() => billingStatus.state.error)

const badgeColor = computed(() => {
  if (!status.value || !status.value.billing_portal_url) return 'grey-darken-1'
  const m = status.value.mode
  const s = status.value.last_heartbeat_status
  // Suspension is a distinct state — never confuse it with "community"
  if (s === 'account_suspended' || s === 'payment_failed') return 'error'
  // "grace_period" is the next-most-urgent state: the customer
  // removed their card entirely.  We give them a yellow warning
  // before on_probation because losing the card is more serious
  // than a recent charge that failed.
  if (s === 'grace_period') return 'warning'
  // "on_probation" is a softer state than payment_failed — the cloud
  // is warning that the customer needs to fix billing soon, but
  // commercial access is still active. Yellow (warning) fits between
  // the green "Commercial" and the red "Payment Failed" states.
  if (s === 'on_probation') return 'warning'
  // The pool-exceeded state is now informational — the cloud
  // auto-bumps the pool to match the camera count, so there's no
  // user action to take.  Show it in the neutral info color rather
  // than error so the sidebar chip doesn't look like an outage.
  if (poolExceeded.value) return 'info'
  if (m === 'commercial') return 'success'
  if (m === 'trial_commercial') {
    return expiringSoon('trial_expires_at') ? 'warning' : 'success'
  }
  // "not_activated" is a normal pre-activation state on a fresh
  // install — the device hasn't been linked to a billing account yet.
  // Show it the same neutral grey as community mode rather than red,
  // which would imply something is broken.
  if (s === 'not_activated') return 'grey-darken-1'
  if (s === 'error') return 'error'
  return 'grey-darken-1'
})

const badgeIcon = computed(() => {
  if (!status.value || !status.value.billing_portal_url) return 'mdi-shield-off-outline'
  const m = status.value.mode
  const s = status.value.last_heartbeat_status
  if (s === 'account_suspended' || s === 'payment_failed') return 'mdi-shield-alert'
  if (s === 'grace_period') return 'mdi-credit-card-off-outline'
  if (s === 'on_probation') return 'mdi-shield-alert-outline'
  if (poolExceeded.value) return 'mdi-cog-outline'
  if (m === 'commercial') return 'mdi-shield-check'
  if (m === 'trial_commercial') return 'mdi-shield-star-outline'
  if (s === 'not_activated') return 'mdi-shield-off-outline'
  if (s === 'error') return 'mdi-shield-alert'
  return 'mdi-shield-off-outline'
})

const badgeLabel = computed(() => {
  if (!status.value || !status.value.billing_portal_url) return 'Community License'
  const s = status.value.last_heartbeat_status
  if (s === 'account_suspended') return 'Suspended'
  if (s === 'payment_failed') return 'Payment Failed'
  if (s === 'grace_period') return 'Grace Period'
  if (s === 'on_probation') return 'On Probation'
  // The cloud now auto-adjusts the pool — the chip tells the user
  // the pool changed, not that something is broken.  Kept short
  // because it lives in a small sidebar chip.
  if (poolExceeded.value) return 'Pool Adjusted'
  const m = status.value.mode
  if (m === 'commercial') return 'Commercial'
  if (m === 'trial_commercial') return 'Trial'
  return 'Community License'
})

const tooltipText = computed(() => {
  if (error.value && !status.value) return `Cannot load license status: ${error.value}`
  if (!status.value) return 'Loading license status…'
  if (!status.value.billing_portal_url) {
    return 'Billing portal is not configured — running on the bundled community license.'
  }
  const m = status.value.mode
  const s = status.value.last_heartbeat_status
  if (s === 'account_suspended') {
    return 'Account suspended by the billing administrator. Contact billing support to restore service. Inference has fallen back to the bundled community model.'
  }
  if (s === 'payment_failed') {
    return 'A recent payment failed. Update your payment method in the billing portal to restore commercial access.'
  }
  if (s === 'on_probation') {
    // Show the date the probation window ends so the user can plan.
    // The cloud caps ``license_expires_at`` at the earliest of
    // trial/grace/probation, so during probation the capped value
    // IS the probation end date
    // (``user.probation_started_at + settings.probation_days``).
    // We also accept the explicit ``probation_ends_at`` field if
    // the cloud starts sending it on the heartbeat in a future
    // release.
    const iso =
      status.value.probation_ends_at || status.value.license_expires_at
    if (iso) {
      const d = new Date(iso)
      if (!isNaN(d.getTime())) {
        const pretty = d.toLocaleDateString(undefined, {
          year: 'numeric', month: 'long', day: 'numeric',
        })
        return `Your billing is on probation. You have commercial access until ${pretty}. Check your credit card in the billing portal to avoid losing access.`
      }
    }
    return 'Your billing is on probation. Check your credit card in the billing portal to avoid losing commercial access.'
  }
  if (s === 'grace_period') {
    // Show the date the grace period ends so the user can plan.
    // Falls back to a generic string if the cloud didn't send a
    // license_expires_at (which would be a cloud bug).
    const iso = status.value.license_expires_at
    if (iso) {
      const d = new Date(iso)
      if (!isNaN(d.getTime())) {
        const pretty = d.toLocaleDateString(undefined, {
          year: 'numeric', month: 'long', day: 'numeric',
        })
        return `Your payment method was removed. Add a new credit card by ${pretty} to keep your commercial license. After that date, the on-prem will fall back to the bundled community model.`
      }
    }
    return 'Your payment method was removed. Add a new credit card in the billing portal to keep your commercial license.'
  }
  if (poolExceeded.value) {
    const used = thisCams.value + otherCams.value
    const q = status.value.quota
    return `Your pool size is automatically adjusted to match your cameras — using ${used} cameras on a pool of ${q}. Your credit card on file will be charged for additional cameras, and you'll receive a statement credit for any unused time when you remove cameras.`
  }
  if (m === 'commercial') {
    if (expiringSoon('license_expires_at')) {
      const days = daysUntil(status.value.license_expires_at)
      return `Commercial license — renews in ${days} day${days === 1 ? '' : 's'}.`
    }
    return 'Commercial license active.'
  }
  if (m === 'trial_commercial') {
    if (expiringSoon('trial_expires_at')) {
      const days = daysUntil(status.value.trial_expires_at)
      return `Trial license — ${days} day${days === 1 ? '' : 's'} left. Add a payment method to continue.`
    }
    return 'Trial license active.'
  }
  if (s === 'not_activated') {
    return 'This device hasn\'t been linked to a billing account yet. Open the License & Billing page to activate, or stay on the bundled community license.'
  }
  if (s === 'error') {
    return 'Cannot reach the billing portal. Check connectivity and try again.'
  }
  if (s === 'auth_failed') {
    return 'Billing portal rejected this device. Try disconnecting and reactivating.'
  }
  return 'Running on the bundled community license. Click to upgrade to commercial.'
})

// Over-quota detection. We compute it from the same fields the
// BillingPage uses so the chip and the page stay in sync.
const thisCams = computed(() => Number(status.value?.this_device_cameras ?? 0))
const otherCams = computed(() => Number(status.value?.other_devices_cameras ?? 0))
const poolExceeded = computed(() => {
  const q = Number(status.value?.quota ?? 0)
  return q > 0 && (thisCams.value + otherCams.value) > q
})
const poolOverage = computed(() => {
  const q = Number(status.value?.quota ?? 0)
  return Math.max(0, (thisCams.value + otherCams.value) - q)
})

function daysUntil(iso) {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return 0
  return Math.ceil(ms / 86400000)
}

function expiringSoon(field) {
  const days = daysUntil(status.value?.[field])
  if (days === null) return false
  return days > 0 && days <= EXPIRING_SOON_DAYS
}

function goToBilling() {
  router.push('/billing')
}

onMounted(() => {
  billingStatus.startPolling()
  // Initial fetch if we don't have anything yet
  if (!billingStatus.state.status && !billingStatus.state.loading) {
    billingStatus.refresh()
  }
})
</script>
