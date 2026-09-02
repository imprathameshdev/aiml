/**
 * Structural tests for the Alerts GUI foundations.
 *
 * Phase H1: nav item, route, permission option, tab persistence.
 * The full page content lands in H2-H5; for H1 we only check the
 * scaffolding (tabs are present, tab state reads from ?tab=, the
 * UserManagement permission list includes manage_alerts, the route
 * is registered).
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC_ALERTS = path.join(__dirname, '..', 'components', 'Alerts.vue')
const SFC_APP = path.join(__dirname, '..', 'App.vue')
const SFC_USER = path.join(__dirname, '..', 'components', 'UserManagement.vue')
const SFC_ROUTER = path.join(__dirname, '..', 'router.js')

const alertsSrc = fs.readFileSync(SFC_ALERTS, 'utf8')
const appSrc = fs.readFileSync(SFC_APP, 'utf8')
const userSrc = fs.readFileSync(SFC_USER, 'utf8')
const routerSrc = fs.readFileSync(SFC_ROUTER, 'utf8')

describe('Alerts.vue scaffolding (H1)', () => {
  it('renders the three tabs (Rules, Channels, History)', () => {
    // Camera Groups was moved to the Cameras page — see the
    // CameraListGroups.spec.js tests for that section.
    expect(alertsSrc).toMatch(/<v-tab[^>]*value="rules"/)
    expect(alertsSrc).toMatch(/<v-tab[^>]*value="channels"/)
    expect(alertsSrc).toMatch(/<v-tab[^>]*value="history"/)
    expect(alertsSrc).not.toMatch(/<v-tab[^>]*value="groups"/)
  })

  it('has v-window + v-window-item for each tab', () => {
    for (const v of ['rules', 'channels', 'history']) {
      expect(
        alertsSrc, `missing v-window-item for ${v}`
      ).toMatch(new RegExp(`<v-window-item[^>]*value="${v}"`))
    }
    // The Camera Groups tab (and its window-item) must be gone.
    expect(alertsSrc).not.toMatch(/value="groups"/)
  })

  it('persists active tab in ?tab= query param', () => {
    expect(alertsSrc).toMatch(/route\.query\.tab/)
    expect(alertsSrc).toMatch(/router\.replace/)
  })

  it('does not import or reference the Camera Groups tab anymore', () => {
    // Belt-and-suspenders against regression: the Alerts page
    // must not import the CameraGroupForm component or any
    // group-related refs/state.  Camera Groups lives on the
    // Cameras page now (see CameraListGroups.spec.js).
    expect(alertsSrc).not.toMatch(/import CameraGroupForm/)
    expect(alertsSrc).not.toMatch(/const groups\s*=/)
    expect(alertsSrc).not.toMatch(/fetchGroups/)
    expect(alertsSrc).not.toMatch(/groupHeaders/)
    expect(alertsSrc).not.toMatch(/openNewGroup/)
    expect(alertsSrc).not.toMatch(/openEditGroup/)
    expect(alertsSrc).not.toMatch(/doDeleteGroup/)
    expect(alertsSrc).not.toMatch(/groupDialogOpen/)
    expect(alertsSrc).not.toMatch(/groupDeleteDialog/)
  })
})

describe('App.vue nav (H1)', () => {
  it('includes an Alerts nav item linking to /alerts', () => {
    expect(appSrc).toMatch(/<v-list-item[^>]*\btitle="Alerts"/)
    expect(appSrc).toMatch(/to="\/alerts"/)
  })

  it('uses a bell icon for the Alerts nav item', () => {
    const m = appSrc.match(/<v-list-item[^>]*\btitle="Alerts"[\s\S]*?>/)
    expect(m, 'Alerts nav item not found').toBeTruthy()
    expect(m[0]).toMatch(/prepend-icon="mdi-bell/)
  })

  it('maps /alerts to the Alerts & Notifications page title', () => {
    expect(appSrc).toMatch(/path\.includes\('alerts'\)[\s\S]{0,80}Alerts & Notifications/)
  })

  it('Alerts nav item is positioned above API Access', () => {
    // Both items are siblings in the sidebar; Alerts must appear
    // before API Access in the source so the rendered order is
    // correct.
    const alertsIdx = appSrc.search(/<v-list-item[^>]*\btitle="Alerts"/)
    const apiIdx = appSrc.search(/<v-list-item[^>]*\btitle="API Access"/)
    expect(alertsIdx, 'Alerts nav item not found').toBeGreaterThan(0)
    expect(apiIdx, 'API Access nav item not found').toBeGreaterThan(0)
    expect(alertsIdx).toBeLessThan(apiIdx)
  })
})

describe('router.js (H1)', () => {
  it('registers the /alerts route', () => {
    expect(routerSrc).toMatch(/\bpath:\s*['"]\/alerts['"]/)
  })

  it('does not require any specific permission for /alerts (page gates internally)', () => {
    // The /alerts route is intentionally open to any authenticated
    // user.  Read-only users see only the History tab.  Pin this
    // so a future refactor doesn't accidentally gate the whole
    // route with requiresPermission: 'manage_alerts'.
    const routeMatch = routerSrc.match(
      /\{[^}]*path:\s*['"]\/alerts['"][^}]*\}/
    )
    expect(routeMatch, '/alerts route block not found').toBeTruthy()
    expect(routeMatch[0]).not.toMatch(/requiresPermission/)
  })
})

describe('UserManagement permission options (H1)', () => {
  it('includes manage_alerts in the permissionOptions array', () => {
    const m = userSrc.match(/permissionOptions\s*=\s*\[[\s\S]*?\]/)
    expect(m, 'permissionOptions not found').toBeTruthy()
    expect(m[0]).toMatch(/value:\s*['"]manage_alerts['"]/)
  })

  it('uses a friendly title for manage_alerts', () => {
    const m = userSrc.match(/permissionOptions\s*=\s*\[[\s\S]*?\]/)
    expect(m, 'permissionOptions not found').toBeTruthy()
    // The entry should have a readable title, not just the raw
    // permission string.
    expect(m[0]).toMatch(/title:\s*['"]Manage Alerts['"]/)
  })
})

describe('Alerts.vue data loading on mount', () => {
  // Regression test for "leave the alerts page and come back, the
  // list is empty; refresh shows it".  The bug was that
  // ``syncTabFromQuery`` only fetched rules/channels when the URL
  // had a ``?tab=`` query param.  When the user navigates to
  // ``/alerts`` from the sidebar (no query), ``activeTab`` defaulted
  // to ``'rules'`` but no fetch was triggered, so the table was
  // empty until the user clicked the refresh button or switched
  // tabs.

  it('syncTabFromQuery fetches rules even when there is no tab query', () => {
    // Extract the function body.  We use a non-greedy match up to
    // the closing brace of the function.
    const m = alertsSrc.match(
      /const syncTabFromQuery\s*=\s*\(\)\s*=>\s*\{[\s\S]*?^\};/m
    )
    expect(m, 'syncTabFromQuery not found').toBeTruthy()
    const body = m[0]
    // The function must call fetchRules at least once.
    const fetchRulesMatches = body.match(/fetchRules\(\)/g) || []
    expect(
      fetchRulesMatches.length,
      'fetchRules not called in syncTabFromQuery'
    ).toBeGreaterThanOrEqual(1)
    // The function must set activeTab from the query param.
    expect(body).toMatch(/activeTab\.value\s*=\s*t/)
  })

  it('onMounted calls syncTabFromQuery so the default tab fetches', () => {
    // onMounted must invoke syncTabFromQuery; combined with the
    // fix above, this ensures rules are fetched on first mount
    // even without a ?tab= query.
    const m = alertsSrc.match(
      /onMounted\(\(\)\s*=>\s*\{[\s\S]*?\}\s*\);/m
    )
    expect(m, 'onMounted block not found').toBeTruthy()
    expect(m[0]).toMatch(/syncTabFromQuery\(\)/)
  })
})
