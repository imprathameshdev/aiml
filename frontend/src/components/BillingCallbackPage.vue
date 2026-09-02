<template>
  <v-container>
    <v-card elevation="2" max-width="600" class="mx-auto mt-12">
      <v-toolbar :color="success ? 'success' : 'error'" density="comfortable">
        <v-toolbar-title class="text-h6 text-white">
          {{ title }}
        </v-toolbar-title>
      </v-toolbar>

      <v-card-text class="pa-6 text-center">
        <v-progress-circular
          v-if="processing"
          indeterminate
          color="primary"
          size="64"
          class="mb-4"
        ></v-progress-circular>

        <v-icon
          v-else-if="success"
          size="96"
          color="success"
          class="mb-4"
        >mdi-check-circle-outline</v-icon>

        <v-icon
          v-else
          size="96"
          color="error"
          class="mb-4"
        >mdi-alert-circle-outline</v-icon>

        <div class="text-h6 mb-2">{{ headline }}</div>
        <div class="text-body-2 text-grey-darken-1 mb-4">{{ subline }}</div>

        <v-alert v-if="errorMessage" type="error" variant="tonal" class="text-left">
          {{ errorMessage }}
        </v-alert>

        <v-btn
          v-if="!processing"
          color="primary"
          class="mt-4"
          @click="goToBilling"
        >
          Go to Billing Dashboard
        </v-btn>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { billingApi } from '../api/billing'
import { billingStatus } from '../api/billingStatus'

const router = useRouter()
const processing = ref(true)
const success = ref(false)
const errorMessage = ref('')
const action = ref('activate') // 'activate' or 'unlink'

const title = computed(() => {
  if (action.value === 'unlink') {
    return processing.value
      ? 'Unlinking Device…'
      : success.value
        ? 'Unlink Complete'
        : 'Unlink Failed'
  }
  return processing.value
    ? 'Activating License…'
    : success.value
      ? 'Activation Complete'
      : 'Activation Failed'
})

const headline = computed(() => {
  if (action.value === 'unlink') {
    if (processing.value) return 'Removing this device from billing…'
    if (success.value) return 'Device unlinked successfully.'
    return 'We could not unlink your device.'
  }
  if (processing.value) return 'Storing your license with this device…'
  if (success.value) return 'License activated successfully.'
  return 'We could not store your license.'
})

const subline = computed(() => {
  if (action.value === 'unlink') {
    if (processing.value) return 'Clearing local session. One moment.'
    if (success.value) return 'Your device has been removed from billing and is running in community mode.'
    return 'Try again from the Billing page, or contact support if this persists.'
  }
  if (processing.value) return 'Talking to the inference engine. One moment.'
  if (success.value) return 'Your device is now linked to the billing portal.'
  return 'Try again from the Billing page, or contact support if this persists.'
})

onMounted(async () => {
  const params = new URLSearchParams(window.location.search)
  const act = params.get('action')
  if (act === 'unlink') {
    action.value = 'unlink'
    try {
      await billingApi.logout()
      await billingStatus.refresh()
      success.value = true
    } catch (e) {
      success.value = false
      errorMessage.value =
        e?.response?.data?.detail || e?.message || 'Unknown error while unlinking.'
    } finally {
      processing.value = false
    }
    return
  }

  const licenseToken = params.get('decryption_key')
  const trialExpiresAt = params.get('trial_expires_at')

  if (!licenseToken) {
    processing.value = false
    success.value = false
    errorMessage.value =
      'No license token was supplied in the redirect URL. Did you arrive at this page through the activation flow?'
    return
  }

  try {
    await billingApi.activateCallback(licenseToken, trialExpiresAt)
    // activate_callback already fired a sync server-side. Pull a
    // fresh status now so the LicenseBadge in the top-left and the
    // BillingPage reflect the new state immediately when the user
    // navigates back — no 60-second poll wait, no hard refresh.
    await billingStatus.refresh()
    success.value = true
  } catch (e) {
    success.value = false
    errorMessage.value =
      e?.response?.data?.detail || e?.message || 'Unknown error while activating.'
  } finally {
    processing.value = false
  }
})

function goToBilling() {
  router.push('/billing')
}
</script>