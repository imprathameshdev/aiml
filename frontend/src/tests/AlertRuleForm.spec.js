/**
 * Structural tests for the Rules tab + AlertRuleForm.
 *
 * Phase H5.1: the six trigger types were collapsed into three
 * top-level "kinds" (per-space, lot_utilization, camera_offline)
 * with a sub-selector for the direction.  Per-space and lot_utilization
 * share a single "Source" selector (Camera or Camera Group).
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC_FORM = path.join(__dirname, '..', 'components', 'AlertRuleForm.vue')
const SFC_ALERTS = path.join(__dirname, '..', 'components', 'Alerts.vue')

const formSrc = fs.readFileSync(SFC_FORM, 'utf8')
const alertsSrc = fs.readFileSync(SFC_ALERTS, 'utf8')

describe('AlertRuleForm.vue — collapsed rule types (H5.1)', () => {
  it('declares exactly three top-level rule kinds', () => {
    const m = formSrc.match(/kindOptions\s*=\s*\[[\s\S]*?\]/)
    expect(m, 'kindOptions not found').toBeTruthy()
    expect(m[0]).toMatch(/value:\s*['"]per_space['"]/)
    expect(m[0]).toMatch(/value:\s*['"]lot_utilization['"]/)
    expect(m[0]).toMatch(/value:\s*['"]camera_offline['"]/)
    // And only those three — no leftover trigger types in the menu.
    expect(m[0]).not.toMatch(/value:\s*['"]space_occupied['"]/)
    expect(m[0]).not.toMatch(/value:\s*['"]space_vacated['"]/)
    expect(m[0]).not.toMatch(/value:\s*['"]space_edge['"]/)
    expect(m[0]).not.toMatch(/value:\s*['"]lot_full_above_pct['"]/)
    expect(m[0]).not.toMatch(/value:\s*['"]lot_open_below_pct['"]/)
  })

  it('rule kind is locked on edit (existing rule\'s category cannot change)', () => {
    // The kind select is :disabled when isEditing.  Changing the
    // kind of an existing rule would silently mutate the trigger
    // type and break saved history references.
    const sel = formSrc.match(/<v-select[\s\S]*?v-model="kind"[\s\S]*?<\/v-select>/)
    expect(sel, 'kind select not found').toBeTruthy()
    expect(sel[0]).toMatch(/:disabled="isEditing"/)
  })

  it('per-space sub-selector iterates Occupied / Vacant / Either', () => {
    // The per-space branch shows a row of v-btn with click handlers.
    // v-chip-group and v-btn-toggle both fail to resolve in this
    // Vuetify 3 build, so we use the lowest-common-denominator
    // pattern: plain v-btn + computed active class.  The titles
    // come from perSpaceSubOptions in the script; pin the data
    // here so a refactor doesn't silently drop a value.
    const block = formSrc.match(
      /<template v-if="kind === 'per_space'">[\s\S]*?<\/template>/
    )
    expect(block, 'per-space template not found').toBeTruthy()
    expect(block[0]).toMatch(/v-for="opt in perSpaceSubOptions"/)
    expect(block[0]).toMatch(/@click="subKind = opt\.value"/)
    // Pin the data array.
    expect(formSrc).toMatch(/perSpaceSubOptions\s*=\s*\[[\s\S]*?\]/)
    expect(formSrc).toMatch(/title:\s*['"]Occupied['"]/)
    expect(formSrc).toMatch(/title:\s*['"]Vacant['"]/)
    expect(formSrc).toMatch(/title:\s*['"]Either['"]/)
    expect(block[0]).not.toMatch(/<v-chip-group/)
    expect(block[0]).not.toMatch(/<v-btn-toggle/)
  })

  it('lot_utilization sub-selector iterates Near full / Near empty', () => {
    const block = formSrc.match(
      /<template v-if="kind === 'lot_utilization'">[\s\S]*?<\/template>/
    )
    expect(block, 'lot_utilization template not found').toBeTruthy()
    expect(block[0]).toMatch(/v-for="opt in lotUtilSubOptions"/)
    expect(block[0]).toMatch(/@click="subKind = opt\.value"/)
    // Pin the data array.
    expect(formSrc).toMatch(/lotUtilSubOptions\s*=\s*\[[\s\S]*?\]/)
    expect(formSrc).toMatch(/Near full \(above %\)/)
    expect(formSrc).toMatch(/Near empty \(below %\)/)
    expect(block[0]).not.toMatch(/<v-chip-group/)
  })

  it('per-space and lot_utilization share a single Source selector component', () => {
    // The plan calls for one Source component used by both rule
    // kinds.  We use two v-btn styled as a segmented control
    // (Camera / Camera group) — v-radio-group / v-radio fail to
    // resolve in this build, so we use v-btn with click handlers.
    const sourceBlocks = (formSrc.match(/sourceType = 'camera'; onSourceTypeChange\(\)/g) || []).length;
    // Both the per-space and lot_utilization branches have one
    // Camera button and one Camera group button = 2 click handlers.
    expect(sourceBlocks).toBe(2);
    // The Source selector must NOT use v-radio-group / v-radio.
    expect(formSrc).not.toMatch(/<v-radio-group/);
    expect(formSrc).not.toMatch(/<v-radio /);
  })

  it('per-space spaces picker filters by the selected source', () => {
    // availableSpaces is a computed that filters spaces by camera_id
    // (when source is Camera) or by group membership (when source
    // is Group).  No per-camera individual selectors.
    const fn = formSrc.match(/const availableSpaces = computed\(\(\) => \{[\s\S]*?\n\}\);/);
    expect(fn, 'availableSpaces not found').toBeTruthy();
    expect(fn[0]).toMatch(/s\.camera_id === pickedSourceId\.value/);
    expect(fn[0]).toMatch(/camIds\.has\(s\.camera_id\)/);
  })

  it('lot_utilization thresholdError depends on subKind direction', () => {
    // The cross-field validation must check the direction the user
    // picked (subKind), not the underlying trigger type.
    const fn = formSrc.match(/const thresholdError = computed\(\(\) => \{[\s\S]*?\n\}\);/);
    expect(fn, 'thresholdError not found').toBeTruthy();
    expect(fn[0]).toMatch(/subKind\.value === 'above'[\s\S]{0,100}less than Fire at %/);
    expect(fn[0]).toMatch(/subKind\.value === 'below'[\s\S]{0,100}greater than Fire at %/);
  });

  it('buildPayload translates (kind, subKind) to (trigger_type, condition)', () => {
    // The translation table is the heart of the collapsed UI.  Pin
    // the mapping so a future refactor doesn't break it.
    const fn = formSrc.match(/const buildPayload = \(\) => \{[\s\S]*?\n\};/);
    expect(fn, 'buildPayload not found').toBeTruthy();
    expect(fn[0]).toMatch(/TRIGGER_FROM_KIND\.per_space/);
    expect(fn[0]).toMatch(/TRIGGER_FROM_KIND\.lot_utilization/);
    // For per-space, only space_ids go into the condition; sourceType
    // is UI-only and not persisted.
    expect(fn[0]).toMatch(/space_ids: \[\.\.\.\(edited\.condition\.space_ids \|\| \[\]\)\]/);
    // For lot_utilization, the source radio determines the scope field.
    expect(fn[0]).toMatch(/condition\.camera_id/);
    expect(fn[0]).toMatch(/condition\.camera_group_id/);
  });

  it('TRIGGER_FROM_KIND and KIND_FROM_TRIGGER are full bidirectional mappings', () => {
    // Each DB trigger_type must round-trip through (kind, subKind).
    expect(formSrc).toMatch(/space_occupied: \['per_space', 'occupied'\]/);
    expect(formSrc).toMatch(/space_vacated: \['per_space', 'vacant'\]/);
    expect(formSrc).toMatch(/space_edge: \['per_space', 'either'\]/);
    expect(formSrc).toMatch(/lot_full_above_pct: \['lot_utilization', 'above'\]/);
    expect(formSrc).toMatch(/lot_open_below_pct: \['lot_utilization', 'below'\]/);
    expect(formSrc).toMatch(/camera_offline: \['camera_offline', '_'\]/);
  });

  it('save posts to /api/alert-rules and puts to /api/alert-rules/{id}', () => {
    expect(formSrc).toMatch(/axios\.post\(['"]\/api\/alert-rules['"]/);
    expect(formSrc).toMatch(/axios\.put\(`\/api\/alert-rules\/\$\{edited\.id\}`/);
  });

  it('Test Fire button is visible only on edit and calls /test-fire', () => {
    const btn = formSrc.match(/<v-btn[\s\S]*?@click="testFire"[\s\S]*?>/);
    expect(btn, 'Test Fire button not found').toBeTruthy();
    expect(btn[0]).toMatch(/v-if="isEditing"/);
    expect(formSrc).toMatch(/axios\.post\(`\/api\/alert-rules\/\$\{edited\.id\}\/test-fire`\)/);
  });

  it('fetchAll loads channels, groups, spaces, and cameras in parallel', () => {
    const fn = formSrc.match(/const fetchAll = async[\s\S]*?\n\};/);
    expect(fn, 'fetchAll not found').toBeTruthy();
    expect(fn[0]).toMatch(/Promise\.all/);
    expect(fn[0]).toMatch(/axios\.get\(['"]\/api\/alert-channels['"]/);
    expect(fn[0]).toMatch(/axios\.get\(['"]\/api\/camera-groups['"]/);
    expect(fn[0]).toMatch(/axios\.get\(['"]\/api\/spaces['"]/);
    expect(fn[0]).toMatch(/axios\.get\(['"]\/api\/cameras['"]/);
  })

  it('new rules default sourceType to "camera" (not camera group)', () => {
    // The simpler scope (single camera) is the more common choice;
    // the operator can switch to "Camera group" if they want.
    // Pin the default ref so a refactor doesn't silently flip it
    // back to 'group'.
    expect(formSrc).toMatch(/const sourceType = ref\(['"]camera['"]\)/);
  })

  it('lot_utilization defaults to "above" (Near Full) when kind changes', () => {
    // onKindChange resets the sub-selector per kind.  Lot
    // utilization should default to "above" (Near Full), which is
    // the more common case than "Near Empty".
    const fn = formSrc.match(/const onKindChange = \(\) => \{[\s\S]*?\n\};/);
    expect(fn, 'onKindChange not found').toBeTruthy();
    expect(fn[0]).toMatch(/lot_utilization['"][\s\S]{0,80}subKind\.value = ['"]above['"]/);
    // And per_space stays at 'occupied' on a kind change.
    expect(fn[0]).toMatch(/per_space['"][\s\S]{0,80}subKind\.value = ['"]occupied['"]/);
  })

  it('lot_utilization Fire/Resolve labels swap with subKind', () => {
    // "Fire above %" / "Resolve below %" for subKind='above' reads
    // naturally for "Near Full".  "Drop below %" / "Recover above %"
    // for subKind='below' reads naturally for "Near Empty".  The
    // arrow icon also flips direction (down for above, up for below)
    // so the visual flow matches the semantics.
    expect(formSrc).toMatch(/const lotFireLabel = computed/);
    expect(formSrc).toMatch(/const lotResolveLabel = computed/);
    expect(formSrc).toMatch(/const lotArrowIcon = computed/);
    // Pin the values: above → "Fire above %" / "Resolve below %";
    // below → "Drop below %" / "Recover above %".  The arrow icon
    // flips to reinforce the visual flow.
    const fireLabelFn = formSrc.match(
      /const lotFireLabel = computed\(\(\) => \{[\s\S]*?\}\);/
    );
    expect(fireLabelFn, 'lotFireLabel not found').toBeTruthy();
    expect(fireLabelFn[0]).toMatch(/'Fire above %'/);
    expect(fireLabelFn[0]).toMatch(/'Drop below %'/);
    const resolveLabelFn = formSrc.match(
      /const lotResolveLabel = computed\(\(\) => \{[\s\S]*?\}\);/
    );
    expect(resolveLabelFn, 'lotResolveLabel not found').toBeTruthy();
    expect(resolveLabelFn[0]).toMatch(/'Resolve below %'/);
    expect(resolveLabelFn[0]).toMatch(/'Recover above %'/);
    const arrowIconFn = formSrc.match(
      /const lotArrowIcon = computed\(\(\) => \{[\s\S]*?\}\);/
    );
    expect(arrowIconFn, 'lotArrowIcon not found').toBeTruthy();
    expect(arrowIconFn[0]).toMatch(/mdi-arrow-down-thick/);
    expect(arrowIconFn[0]).toMatch(/mdi-arrow-up-thick/);
    // Bind the labels in the lot_utilization EDITOR block (not the
    // sub-selector).  The editor is the second ``<template
    // v-if="kind === 'lot_utilization'">`` in the file; we match by
    // a unique anchor (the v-row containing the two v-text-fields).
    const editorBlock = formSrc.match(
      /<template v-if="kind === 'lot_utilization'">[\s\S]*?<v-row dense>[\s\S]*?<\/template>/
    );
    expect(editorBlock, 'lot_utilization editor block not found').toBeTruthy();
    expect(editorBlock[0]).toMatch(
      /v-model\.number="edited\.condition\.fire_at_pct"[\s\S]*?:label="lotFireLabel"/
    );
    expect(editorBlock[0]).toMatch(
      /v-model\.number="edited\.condition\.resolve_at_pct"[\s\S]*?:label="lotResolveLabel"/
    );
    expect(editorBlock[0]).toMatch(/\{\{ lotArrowIcon \}\}/);
    // No leftover hardcoded "Fire at %" / "Resolve at %" labels in
    // the lot_utilization editor (they're now computed).
    expect(editorBlock[0]).not.toMatch(/label="Fire at %"/);
    expect(editorBlock[0]).not.toMatch(/label="Resolve at %"/);
  })

  it('Test Fire button works for camera_offline rules too (not just per-space)', () => {
    // The Test Fire button is shown for all rule kinds in edit
    // mode.  Pin that the button is unconditional on kind — a
    // future refactor that adds a v-if="kind === 'per_space'" gate
    // would break the camera-offline test-fire path.
    // Find the exact v-btn that contains @click="testFire".  Using
    // a generic <v-btn...@click="testFire"...> regex would start at
    // the first <v-btn (a sub-selector) and end at the first `>`
    // after `testFire`, spanning unrelated content.  Instead, look
    // for the v-card-actions block, which contains the Test Fire
    // button.
    const actionsBlock = formSrc.match(
      /<v-card-actions>[\s\S]*?<\/v-card-actions>/
    );
    expect(actionsBlock, 'v-card-actions not found').toBeTruthy();
    const btn = actionsBlock[0].match(
      /<v-btn[\s\S]*?@click="testFire"[\s\S]*?>/,
    );
    expect(btn, 'Test Fire button not found').toBeTruthy();
    expect(btn[0]).toMatch(/v-if="isEditing"/);
    expect(btn[0]).toMatch(/@click="testFire"/);
    // The button must NOT be gated on a specific kind.
    expect(btn[0]).not.toMatch(/v-if="kind === 'per_space'"/);
    expect(btn[0]).not.toMatch(/v-if="kind === 'lot_utilization'"/);
    expect(btn[0]).not.toMatch(/v-if="kind === 'camera_offline'"/);
  })

  it('awaits fetchAll and calls resetFromRule so camera/spaces are resolved on dialog open', () => {
    expect(formSrc).toMatch(/watch\(\s*\(\)\s*=>\s*props\.modelValue,\s*async/);
    expect(formSrc).toMatch(/await\s+fetchAll\(\)[\s\S]*?resetFromRule\(\)/);
  })
})


describe('Alerts.vue — Rules tab labels (H5.1)', () => {
  it('shows user-facing trigger labels (collapsed categories)', () => {
    // The DB still stores the old trigger_type values; the table
    // displays the user-facing label.
    expect(alertsSrc).toMatch(/per-space \(occupied\)/);
    expect(alertsSrc).toMatch(/per-space \(vacant\)/);
    expect(alertsSrc).toMatch(/per-space \(either\)/);
    expect(alertsSrc).toMatch(/lot utilization \(above %\)/);
    expect(alertsSrc).toMatch(/lot utilization \(below %\)/);
  });

  it('condition summary shows the scope (camera or group) for threshold rules', () => {
    // The summary must distinguish camera_id vs camera_group_id
    // so the operator can tell which scope the rule covers.
    const fn = alertsSrc.match(/const conditionSummary = \(item\) => \{[\s\S]*?\n\};/);
    expect(fn, 'conditionSummary not found').toBeTruthy();
    expect(fn[0]).toMatch(/c\.camera_group_id/);
    expect(fn[0]).toMatch(/c\.camera_id/);
  });
})
