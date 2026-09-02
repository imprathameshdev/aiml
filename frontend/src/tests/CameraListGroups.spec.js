/**
 * Structural tests for the Camera Groups section in CameraList.vue.
 *
 * The Camera Groups section was moved from the Alerts page to the
 * Cameras page so the concept lives next to the cameras it groups.
 * The backend endpoint, the CameraGroupForm dialog component, and
 * the AlertRuleForm integration are all unchanged.  This file
 * pins the new location's UI scaffolding.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC = path.join(__dirname, '..', 'components', 'CameraList.vue')

const src = fs.readFileSync(SFC, 'utf8')

describe('CameraList.vue — Camera Groups section', () => {
  it('renders the Camera Groups card with a header and Add Group button', () => {
    expect(src).toMatch(/Camera Groups/)
    // Add Group button is gated by manage_cameras (the same
    // permission that gates camera CRUD on this page).
    const addBtn = src.match(/<v-btn[^>]*@click="openNewGroup"[\s\S]*?>/)
    expect(addBtn, 'Add Group button not found').toBeTruthy()
    expect(addBtn[0]).toMatch(/v-if="hasPermission\('manage_cameras'\)"/)
  })

  it('renders the groups data table with the expected columns', () => {
    const headersMatch = src.match(/groupHeaders\s*=\s*\[[\s\S]*?\]/)
    expect(headersMatch, 'groupHeaders not found').toBeTruthy()
    expect(headersMatch[0]).toMatch(/key:\s*['"]name['"]/)
    expect(headersMatch[0]).toMatch(/key:\s*['"]camera_count['"]/)
    expect(headersMatch[0]).toMatch(/key:\s*['"]actions['"]/)
  })

  it('fetches groups from /api/camera-groups', () => {
    expect(src).toMatch(/axios\.get\(['"]\/api\/camera-groups['"]\)/)
  })

  it('Edit and Delete actions in the group table are gated by manage_cameras', () => {
    // Both action buttons live inside a #item.actions template
    // whose wrapper has v-if="hasPermission('manage_cameras')".
    const actions = src.match(
      /<template #item\.actions="\{ item \}">[\s\S]*?<\/template>/
    )
    expect(actions, 'actions template not found').toBeTruthy()
    expect(actions[0]).toMatch(/v-if="hasPermission\('manage_cameras'\)"/)
  })

  it('delete group handler surfaces the server detail on 409', () => {
    expect(src).toMatch(/doDeleteGroup/)
    // The 409 detail ("referenced by rule X") should be shown
    // verbatim via the snackbar.
    expect(src).toMatch(/e\.response\?\.data\?\.detail/)
  })

  it('uses the CameraGroupForm dialog component', () => {
    // The form dialog is imported and used in the template.
    expect(src).toMatch(/import CameraGroupForm/)
    expect(src).toMatch(/<CameraGroupForm\b/)
    expect(src).toMatch(/v-model="groupDialogOpen"/)
  })

  it('fetches groups on mount alongside cameras', () => {
    expect(src).toMatch(/onMounted\(\(\) => \{[\s\S]*fetchCameras\(\)[\s\S]*fetchGroups\(\)/)
  })
})
