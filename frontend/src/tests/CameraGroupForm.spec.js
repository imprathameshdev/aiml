/**
 * Structural tests for the CameraGroupForm dialog component.
 *
 * The Camera Groups section itself (data table, edit/delete actions,
 * empty state) was moved from the Alerts page to the Cameras page
 * in a follow-up change.  See CameraListGroups.spec.js for tests of
 * that section; this file only tests the form dialog itself.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC_FORM = path.join(__dirname, '..', 'components', 'CameraGroupForm.vue')

const formSrc = fs.readFileSync(SFC_FORM, 'utf8')

describe('CameraGroupForm.vue (H3)', () => {
  it('has a name field and a camera multi-select', () => {
    expect(formSrc).toMatch(/v-model="edited\.name"/)
    expect(formSrc).toMatch(/<v-text-field[^>]*v-model="edited\.name"/)
    expect(formSrc).toMatch(/v-model="edited\.camera_ids"/)
    expect(formSrc).toMatch(/<v-select[^>]*v-model="edited\.camera_ids"/)
  })

  it('camera select is multi-select with chips', () => {
    const sel = formSrc.match(/<v-select[\s\S]*?v-model="edited\.camera_ids"[\s\S]*?>/)
    expect(sel, 'camera select not found').toBeTruthy()
    expect(sel[0]).toMatch(/multiple/)
    expect(sel[0]).toMatch(/chips/)
  })

  it('fetches the camera list from /api/cameras', () => {
    expect(formSrc).toMatch(/axios\.get\(['"]\/api\/cameras['"]\)/)
  })

  it('save posts to /api/camera-groups and puts to /api/camera-groups/{id}', () => {
    expect(formSrc).toMatch(/axios\.post\(['"]\/api\/camera-groups['"]/)
    expect(formSrc).toMatch(/axios\.put\(`\/api\/camera-groups\/\$\{edited\.id\}`/)
  })

  it('requires at least one camera', () => {
    expect(formSrc).toMatch(/At least one camera is required/)
  })

  it('forbids editing the name of a saved group? No — name is editable on existing groups', () => {
    // The CameraGroupForm allows editing the name.  Only the
    // channel type is locked on edit.  Pin the asymmetry.
    const nameField = formSrc.match(/<v-text-field[^>]*v-model="edited\.name"[\s\S]*?>/)
    expect(nameField, 'name field not found').toBeTruthy()
    expect(nameField[0]).not.toMatch(/:disabled="isEditing"/)
  })
})
