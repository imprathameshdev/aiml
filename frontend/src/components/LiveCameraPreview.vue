<template>
  <div class="live-preview">
    <img v-if="imageUrl" :src="imageUrl" alt="Live camera preview" @load="onImageLoad" />
    <span class="preview-status" :class="{ connected: status === 'Live' }">{{ status }}</span>
  </div>
</template>
<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue';
const props = defineProps({ cameraId: Number, active: Boolean, width: { type: Number, default: 960 } });
const emit = defineEmits(['dimensions']);
const imageUrl = ref('');
const status = ref('Connecting');
const visible = ref(!document.hidden);
let controller, retryTimer, watchdog, pendingUrl = '', lastFrame = 0, stopped = false;
function onImageLoad(event) {
  emit('dimensions', event.target);
  if (pendingUrl && pendingUrl !== imageUrl.value) URL.revokeObjectURL(pendingUrl);
  pendingUrl = imageUrl.value;
}
function stop() {
  stopped = true;
  controller?.abort();
  clearTimeout(retryTimer);
  clearInterval(watchdog);
}
async function connect() {
  stop();
  if (!props.active || !visible.value) { status.value = 'Paused'; return; }
  stopped = false;
  const current = new AbortController();
  controller = current;
  status.value = 'Connecting';
  lastFrame = Date.now();
  watchdog = setInterval(() => {
    if (Date.now() - lastFrame > 6000) { status.value = 'Reconnecting'; current.abort(); }
  }, 1000);
  try {
    const token = localStorage.getItem('token');
    const response = await fetch(`/api/cameras/${props.cameraId}/preview?width=${props.width}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: current.signal, cache: 'no-store'
    });
    if (!response.ok) {
      if ([401, 403, 404, 400].includes(response.status)) {
        status.value = response.status === 401 ? 'Sign in to view live video' : 'Preview unavailable';
        stopped = true;
      }
      throw new Error('Preview unavailable');
    }
    const reader = response.body.getReader();
    let buffer = new Uint8Array(0);
    try {
      while (!current.signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        const next = new Uint8Array(buffer.length + value.length);
        next.set(buffer); next.set(value, buffer.length); buffer = next;
        let start = -1, consumed = 0, latest = null;
        // JPEG markers remain valid across arbitrary network chunk boundaries.
        for (let i = 0; i < buffer.length - 1; i++) {
          if (start < 0 && buffer[i] === 255 && buffer[i+1] === 216) start = i;
          if (start >= 0 && buffer[i] === 255 && buffer[i+1] === 217) {
            latest = buffer.slice(start, i+2); consumed = i+2; start = -1; i++;
          }
        }
        if (latest) {
          if (imageUrl.value && imageUrl.value !== pendingUrl) URL.revokeObjectURL(imageUrl.value);
          imageUrl.value = URL.createObjectURL(new Blob([latest], { type: 'image/jpeg' }));
          lastFrame = Date.now(); status.value = 'Live';
        }
        buffer = buffer.slice(consumed);
        if (buffer.length > 4 * 1024 * 1024) throw new Error('Invalid preview frame');
      }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  } catch (_) { /* Status and reconnect are handled below. */ }
  finally {
    if (controller === current) {
      clearInterval(watchdog);
      if (!stopped && props.active && visible.value) {
        status.value = 'Reconnecting'; retryTimer = setTimeout(connect, 2000);
      }
    }
  }
}
function visibilityChanged() { visible.value = !document.hidden; }
watch(() => [props.active, props.cameraId, props.width, visible.value], connect);
onMounted(() => { document.addEventListener('visibilitychange', visibilityChanged); connect(); });
onUnmounted(() => {
  stop(); document.removeEventListener('visibilitychange', visibilityChanged);
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
  if (pendingUrl && pendingUrl !== imageUrl.value) URL.revokeObjectURL(pendingUrl);
});
</script>
<style scoped>
.live-preview { position:absolute; inset:0; z-index:0; background:#000; pointer-events:none; }
.live-preview img { width:100%; height:100%; object-fit:contain; display:block; }
.preview-status { position:absolute; right:8px; bottom:8px; padding:3px 8px; background:#333d; color:white; border-radius:12px; font-size:12px; }
.preview-status.connected { background:#16713bdd; }
</style>
