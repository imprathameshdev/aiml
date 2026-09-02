<template>
  <v-app class="app-container">
    <!-- Modern Floating Sidebar -->
    <v-navigation-drawer
      v-if="!isPublicPage"
      permanent 
      elevation="0"
      class="sidebar-drawer border-0"
      :width="260"
    >
      <div class="pa-6 pb-2 d-flex align-center">
        <v-avatar size="40" rounded="lg" class="mr-3 elevation-4 brand-logo">
          <img src="/logo_just_vulture.png" style="width: 100%; height: 100%; object-fit: cover;">
        </v-avatar>
        <div>
          <div class="text-h6 font-weight-black line-height-1 color-white">LOT VULTURE</div>
          <div class="text-caption color-grey-lighten-1 mt-n1">Circling the Parking Lot</div>
        </div>
      </div>

      <div class="px-6 pb-2">
        <LicenseBadge v-if="hasPermission('manage_billing')" />
      </div>

      <div class="pa-4 pt-6">
        <v-list density="compact" nav class="pa-0 main-nav">
          <div class="nav-subheader text-uppercase font-weight-bold letter-spacing-1 mb-2 px-4">Monitor</div>
          
          <v-list-item 
            prepend-icon="mdi-view-dashboard-outline" 
            title="Live Dashboard" 
            to="/dashboard" 
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item 
            v-if="hasPermission('view_history')" 
            prepend-icon="mdi-history" 
            title="History Review" 
            to="/history"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item 
            v-if="hasPermission('view_metrics')" 
            prepend-icon="mdi-chart-box-outline" 
            title="System Metrics" 
            to="/metrics"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <div class="nav-subheader text-uppercase font-weight-bold letter-spacing-1 mt-6 mb-2 px-4">Configure</div>

          <v-list-item 
            v-if="hasPermission('manage_cameras')" 
            prepend-icon="mdi-camera-outline" 
            title="Cameras & Spaces" 
            to="/cameras"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item
            prepend-icon="mdi-bell-ring-outline"
            title="Alerts"
            to="/alerts"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item 
            v-if="hasPermission('manage_api_keys')" 
            prepend-icon="mdi-api" 
            title="API Access" 
            to="/access"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item 
            v-if="hasPermission('manage_users')" 
            prepend-icon="mdi-account-group-outline" 
            title="Users" 
            to="/users"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item 
            v-if="hasPermission('edit_settings')" 
            prepend-icon="mdi-cog-outline" 
            title="Settings" 
            to="/settings"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item
            v-if="hasPermission('manage_billing')"
            prepend-icon="mdi-license"
            title="License & Billing"
            to="/billing"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>

          <v-list-item 
            v-if="hasPermission('view_diagnostics')" 
            prepend-icon="mdi-shield-bug-outline" 
            title="Diagnostics" 
            to="/diagnostics"
            class="nav-item mb-1"
            rounded="lg"
            color="primary"
          ></v-list-item>
        </v-list>
      </div>

      <template v-slot:append>
        <div class="pa-4 text-center">
          <div class="sidebar-clock d-flex align-center justify-center rounded-pill mb-2">
            <v-icon start size="small">mdi-clock-outline</v-icon>
            <span class="text-caption font-weight-bold">{{ currentTime }}</span>
          </div>
          <div v-if="appVersion" class="text-caption text-grey text-truncate px-1" style="font-size: 0.72rem !important; opacity: 0.85;">
            <span>LotVulture {{ versionPrefix }}</span><a
              v-if="githubUrl && commitSha"
              :href="githubUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="text-grey-lighten-1 text-decoration-underline"
              style="font-family: monospace;"
              title="View commit on GitHub"
            >{{ commitSha }}</a>
          </div>
        </div>
      </template>
    </v-navigation-drawer>

    <!-- Modern Header -->
    <v-app-bar v-if="!isPublicPage" elevation="0" class="app-header border-b px-4">
      <v-btn icon="mdi-menu" variant="text" class="d-md-none mr-2"></v-btn>
      <div class="text-h6 font-weight-medium d-none d-sm-block">{{ pageTitle }}</div>

      <v-spacer></v-spacer>

      <v-menu location="bottom end" origin="top end" :close-on-content-click="false">
        <template v-slot:activator="{ props }">
          <v-btn
            v-bind="props"
            variant="text"
            class="user-menu-btn px-2"
            :ripple="true"
          >
            <div class="d-flex align-center">
              <v-avatar color="primary" size="32" class="mr-2">
                <span class="text-caption font-weight-bold">{{ username?.charAt(0).toUpperCase() }}</span>
              </v-avatar>
              <div class="d-none d-sm-block text-left">
                <div class="text-body-2 font-weight-bold text-grey-darken-3 line-height-1">{{ username }}</div>
                <div class="text-caption text-grey-darken-1 line-height-1">{{ roleLabel }}</div>
              </div>
              <v-icon size="small" color="grey-darken-1" class="ml-1">mdi-chevron-down</v-icon>
            </div>
          </v-btn>
        </template>
        <v-card rounded="lg" elevation="8" min-width="220">
          <v-list density="compact" class="pa-1">
            <v-list-item @click="showPasswordDialog = true" prepend-icon="mdi-lock-reset" title="Change Password" rounded="lg"></v-list-item>
            <v-divider class="my-1"></v-divider>
            <v-list-item @click="logout" prepend-icon="mdi-logout" title="Logout" color="error" rounded="lg"></v-list-item>
          </v-list>
        </v-card>
      </v-menu>
    </v-app-bar>

    <!--
      ``scrollable`` enables proper internal scrolling on long pages
      like Settings. The Login page uses ``<v-container class="fill-height">``
      (``height: 100%``) to vertically center its card. ``height: 100%``
      resolves correctly only when v-main's slot is the direct child
      (the default), so we keep v-main non-scrollable on the login
      route — the login form is short enough to fit any viewport.
    -->
    <v-main
      :scrollable="!isPublicPage"
      :class="['main-content', isPublicPage ? 'pa-0' : 'content-bg']"
    >
      <div :class="['page-wrapper', { 'pa-6': !isPublicPage && !isHistoryPage }]">
        <router-view />
      </div>
    </v-main>

    <ChangePasswordDialog v-model="showPasswordDialog" />
  </v-app>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue';
import axios from 'axios';
import { useRouter, useRoute } from 'vue-router';
import ChangePasswordDialog from './components/ChangePasswordDialog.vue';
import LicenseBadge from './components/LicenseBadge.vue';

const router = useRouter();
const route = useRoute();
const showPasswordDialog = ref(false);
const username = ref(localStorage.getItem('username') || 'User');
const isAdmin = ref(localStorage.getItem('is_admin') === 'true');
const currentTime = ref('');
const appVersion = ref('');
const commitSha = ref('');
const githubUrl = ref('');
const versionPrefix = computed(() => {
  if (!appVersion.value) return '';
  if (commitSha.value && appVersion.value.endsWith(`-${commitSha.value}`)) {
    return appVersion.value.slice(0, -commitSha.value.length);
  }
  return appVersion.value;
});
let timer = null;

const isLoginPage = computed(() => route.path === '/');
const isPublicPage = computed(() => route.path === '/' || route.path === '/setup');
const isHistoryPage = computed(() => route.path === '/history');

const roleLabel = computed(() => isAdmin.value ? 'Administrator' : 'Operator');

const pageTitle = computed(() => {
  const path = route.path;
  if (path.includes('dashboard')) return 'Live Monitor';
  if (path.includes('history')) return 'Visual History';
  if (path.includes('metrics')) return 'Performance Analytics';
  if (path.includes('cameras')) return 'Camera Management';
  if (path.includes('editor')) return 'Space Designer';
  if (path.includes('access')) return 'API Access Control';
  if (path.includes('users')) return 'User Management';
  if (path.includes('settings')) return 'System Settings';
  if (path.includes('alerts')) return 'Alerts & Notifications';
  return 'Lot Vulture';
});

const updateClock = () => {
  const now = new Date();
  currentTime.value = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const logout = () => {
  localStorage.removeItem('token');
  localStorage.removeItem('permissions');
  localStorage.removeItem('username');
  localStorage.removeItem('is_admin');
  delete axios.defaults.headers.common['Authorization'];
  router.push('/');
};

const hasPermission = (p) => {
  if (isLoginPage.value) return false;
  if (localStorage.getItem('is_admin') === 'true') return true;
  const permsStr = localStorage.getItem('permissions');
  if (!permsStr) return false;
  try {
    const perms = JSON.parse(permsStr);
    return perms.includes(p);
  } catch (e) {
    return false;
  }
};

const syncUser = async () => {
  if (isPublicPage.value) return;
  const token = localStorage.getItem('token');
  if (!token) return;

  try {
    const res = await axios.get('/api/users/me');
    localStorage.setItem('permissions', JSON.stringify(res.data.permissions));
    localStorage.setItem('is_admin', res.data.is_admin ? 'true' : 'false');
    isAdmin.value = res.data.is_admin;
    username.value = res.data.username;
    localStorage.setItem('username', res.data.username);
  } catch (error) {
    if (error.response?.status === 401) {
      logout();
    }
  }
};

watch(isLoginPage, async (val) => {
  if (!val) {
    await syncUser();
  }
});

onMounted(async () => {
  updateClock();
  timer = setInterval(updateClock, 1000);
  await syncUser();
  try {
    const vRes = await axios.get('/api/version');
    appVersion.value = vRes.data.version || '';
    commitSha.value = vRes.data.commit || '';
    githubUrl.value = vRes.data.github_url || '';
  } catch (e) {
    console.debug('Failed to fetch app version in sidebar', e);
  }
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<style>
/* Modern Overrides */
.app-container {
  background-color: #f8fafc !important;
}

.sidebar-drawer {
  background-color: #0f172a !important; /* Slate 900 */
}

.color-white {
  color: white !important;
}

.color-grey-lighten-1 {
  color: #cbd5e1 !important; /* Slate 300 */
}

.nav-subheader {
  color: #64748b !important; /* Slate 500 */
  font-size: 0.7rem !important;
  letter-spacing: 0.1em !important;
}

.brand-logo {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
}

.nav-item {
  color: #94a3b8 !important; /* Slate 400 */
  transition: all 0.2s ease;
}

.nav-item:hover {
  background-color: rgba(255, 255, 255, 0.05) !important;
  color: white !important;
}

.nav-item.v-list-item--active {
  background-color: #3b82f6 !important;
  color: white !important;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
}

.app-header {
  background-color: rgba(255, 255, 255, 0.8) !important;
  backdrop-filter: blur(10px);
}

.content-bg {
  background-color: #f1f5f9 !important; /* Slate 100 */
}

.user-profile-card {
  transition: all 0.2s ease;
  border: 1px solid rgba(255, 255, 255, 0.05) !important;
}

.user-profile-card:hover {
  background-color: rgba(255, 255, 255, 0.08) !important;
}

.line-height-1 {
  line-height: 1;
}

.letter-spacing-1 {
  letter-spacing: 1px;
}

/* Clock chip at the bottom of the dark sidebar — light text on dark bg */
.sidebar-clock {
  background-color: rgba(255, 255, 255, 0.06) !important;
  color: #cbd5e1 !important; /* Slate 300 */
  padding: 8px 16px;
  border: 1px solid rgba(255, 255, 255, 0.05);
}

/* Top-right user dropdown button — keeps the avatar visible against
   the semi-transparent app-bar background */
.user-menu-btn {
  text-transform: none !important;
  height: auto !important;
  padding-top: 4px !important;
  padding-bottom: 4px !important;
}

/* Scrollbar refinement */
::-webkit-scrollbar {
  width: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.1);
  border-radius: 10px;
}
::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.2);
}

.v-navigation-drawer::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1);
}

/* Wrapper fills v-main's height and provides its own scrollbar when
   content overflows. The ``height: 100%`` is required by the Login
   page so its inner ``<v-container class="fill-height">`` can resolve
   ``height: 100%`` for vertical centering. ``overflow-y: auto`` is the
   fallback for long pages like Settings whose content would otherwise
   be clipped.

   The history page sets its own full-screen height via inline style
   (``height: calc(100vh - 100px)``) and does not depend on the
   wrapper's height. */
.page-wrapper {
  height: 100%;
  overflow-y: auto;
}
</style>
