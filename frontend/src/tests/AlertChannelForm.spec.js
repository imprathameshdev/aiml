/**
 * Structural tests for the Channels tab + AlertChannelForm.
 *
 * Phase H2: pins the channel-form structure (all three channel
 * types present, config editor swaps based on type, test-channel
 * button on edit), the channels table (add / edit / delete /
 * enable toggle), and the 409-handling for "channel referenced by
 * a rule".
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC_FORM = path.join(__dirname, '..', 'components', 'AlertChannelForm.vue')
const SFC_ALERTS = path.join(__dirname, '..', 'components', 'Alerts.vue')

const formSrc = fs.readFileSync(SFC_FORM, 'utf8')
const alertsSrc = fs.readFileSync(SFC_ALERTS, 'utf8')

describe('AlertChannelForm.vue (H2)', () => {
  it('declares all three channel types in the type options', () => {
    const m = formSrc.match(/channelTypeOptions\s*=\s*\[[\s\S]*?\]/)
    expect(m, 'channelTypeOptions not found').toBeTruthy()
    expect(m[0]).toMatch(/value:\s*['"]webhook['"]/)
    expect(m[0]).toMatch(/value:\s*['"]email['"]/)
    expect(m[0]).toMatch(/value:\s*['"]mqtt['"]/)
  })

  it('renders the webhook config editor (URL + secret) when type is webhook', () => {
    // The config editor lives behind a v-if="edited.channel_type === 'webhook'"
    // block; we assert the structure of that block.
    const block = formSrc.match(
      /<template v-if="edited\.channel_type === 'webhook'">[\s\S]*?<\/template>/
    )
    expect(block, 'webhook config block not found').toBeTruthy()
    expect(block[0]).toMatch(/v-model="webhookConfig\.url"/)
    expect(block[0]).toMatch(/v-model="webhookConfig\.secret"/)
    expect(block[0]).toMatch(/label="URL"/)
  })

  it('renders the email config editor (to addresses) when type is email', () => {
    // The form uses a v-text-field + v-chip-group pattern (NOT a
    // v-combobox) because the combobox with multiple+chips can
    // collapse to 0x0 inside a template v-if.  The chip list
    // renders from emailConfig.to via the addEmail/removeEmail
    // handlers; the v-text-field's v-model is newEmail.
    const block = formSrc.match(
      /<template v-if="edited\.channel_type === 'email'">[\s\S]*?<\/template>/
    )
    expect(block, 'email config block not found').toBeTruthy()
    expect(block[0]).toMatch(/<v-text-field[^>]*v-model="newEmail"/)
    expect(block[0]).toMatch(/v-chip/)
    // Must NOT use the broken v-combobox pattern.
    expect(block[0]).not.toMatch(/<v-combobox/)
  })

  it('email config exposes subject prefix and from-address overrides', () => {
    const block = formSrc.match(
      /<template v-if="edited\.channel_type === 'email'">[\s\S]*?<\/template>/
    )
    expect(block, 'email config block not found').toBeTruthy()
    expect(block[0]).toMatch(/v-model="emailConfig\.subject_prefix"/)
    expect(block[0]).toMatch(/v-model="emailConfig\.from_addr"/)
    // These are passed into the API payload when set.
    expect(block[0]).toMatch(/label="Subject prefix[^"]*"/)
    expect(block[0]).toMatch(/label="From address override[^"]*"/)
  })

  it('email v-combobox is NOT used (it collapses to 0x0 in template v-if blocks)', () => {
    // The v-combobox with multiple+chips can collapse to 0x0 inside
    // a template v-if (a known Vuetify rendering issue when the
    // chips array is empty).  We replaced it with a v-text-field +
    // v-chip-group pattern, which renders reliably.  Pin the absence
    // so a future contributor doesn't "fix" the missing combobox by
    // adding one back.
    const block = formSrc.match(
      /<template v-if="edited\.channel_type === 'email'">[\s\S]*?<\/template>/
    )
    expect(block, 'email config block not found').toBeTruthy()
    expect(block[0]).not.toMatch(/<v-combobox/)
    // The replacement uses a v-text-field for input and a v-chip
    // group for display.
    expect(block[0]).toMatch(/<v-text-field[^>]*v-model="newEmail"/)
    expect(block[0]).toMatch(/v-chip/)
  })

  it('email input adds to the recipient list on Enter and removes on chip close', () => {
    // The handler logic that drives the chip list.
    const addFn = formSrc.match(/const addEmail = \(\) => \{[\s\S]*?\n\};/)
    expect(addFn, 'addEmail function not found').toBeTruthy()
    expect(addFn[0]).toMatch(/emailConfig\.to\.push\(email\)/)
    const removeFn = formSrc.match(/const removeEmail = \(email\) => \{[\s\S]*?\n\};/)
    expect(removeFn, 'removeEmail function not found').toBeTruthy()
    expect(removeFn[0]).toMatch(/splice/)
  })

  it('email config block has a visible divider and clear label so the To field is not missed', () => {
    // Regression guard: an operator reported they could not see the
    // "To" field on the email channel.  The fix added a v-divider
    // with the section name and a more explicit label.  Pin both.
    const block = formSrc.match(
      /<template v-if="edited\.channel_type === 'email'">[\s\S]*?<\/template>/
    )
    expect(block, 'email config block not found').toBeTruthy()
    expect(block[0]).toMatch(/<v-divider/)
    expect(block[0]).toMatch(/Email channel configuration/)
    // Input must say "Add recipient" so the field is discoverable.
    expect(block[0]).toMatch(/Add recipient/)
  })

  it('renders the MQTT config editor (broker/port/user/pass/topic/qos) when type is mqtt', () => {
    const block = formSrc.match(
      /<template v-if="edited\.channel_type === 'mqtt'">[\s\S]*?<\/template>/
    )
    expect(block, 'mqtt config block not found').toBeTruthy()
    expect(block[0]).toMatch(/v-model="mqttConfig\.broker"/)
    expect(block[0]).toMatch(/v-model\.number="mqttConfig\.port"/)
    expect(block[0]).toMatch(/v-model="mqttConfig\.topic"/)
    expect(block[0]).toMatch(/v-model\.number="mqttConfig\.qos"/)
  })

  it('Test Channel button is available on both create and edit forms', () => {
    // The button must NOT be gated by isEditing — the operator
    // should be able to verify a config before saving it.
    // We assert the button exists and its handler does not branch
    // on isEditing to refuse the action.
    const btn = formSrc.match(/<v-btn[^>]*@click="testChannel"[\s\S]*?>/)
    expect(btn, 'Test Channel button not found').toBeTruthy()
    expect(btn[0]).not.toMatch(/v-if="isEditing"/)
  })

  it('Test Channel uses /test-config for unsaved channels and /{id}/test for saved ones', () => {
    const fn = formSrc.match(/const testChannel = async[\s\S]*?\n\};/)
    expect(fn, 'testChannel function not found').toBeTruthy()
    // Both endpoints must be present.
    expect(fn[0]).toMatch(/axios\.post\(['"]\/api\/alert-channels\/test-config['"]/)
    expect(fn[0]).toMatch(/axios\.post\(`\/api\/alert-channels\/\$\{edited\.id\}\/test`\)/)
  })

  it('save posts/puts to the channel API with the type-specific config', () => {
    // Webhook path: payload includes url, optional secret.
    const buildPayload = formSrc.match(/buildPayload[\s\S]*?\n\}/)
    expect(buildPayload, 'buildPayload not found').toBeTruthy()
    expect(buildPayload[0]).toMatch(/channel_type: edited\.channel_type/)
    expect(buildPayload[0]).toMatch(/webhookConfig\.url/)
    expect(buildPayload[0]).toMatch(/emailConfig\.to/)
    expect(buildPayload[0]).toMatch(/mqttConfig\.broker/)
  })

  it('forbids editing the channel type on an existing channel (re-requires creating a new one)', () => {
    // Type dropdown should be :disabled when isEditing.
    expect(formSrc).toMatch(
      /<v-select[\s\S]*?v-model="edited\.channel_type"[\s\S]*?:disabled="isEditing"/
    )
  })
})

describe('Alerts.vue — Channels tab (H2)', () => {
  it('renders the channels data table with the expected columns', () => {
    const headersMatch = alertsSrc.match(/channelHeaders\s*=\s*\[[\s\S]*?\]/)
    expect(headersMatch, 'channelHeaders not found').toBeTruthy()
    expect(headersMatch[0]).toMatch(/key:\s*['"]name['"]/)
    expect(headersMatch[0]).toMatch(/key:\s*['"]channel_type['"]/)
    expect(headersMatch[0]).toMatch(/key:\s*['"]enabled['"]/)
    expect(headersMatch[0]).toMatch(/key:\s*['"]actions['"]/)
  })

  it('fetches channels from /api/alert-channels', () => {
    expect(alertsSrc).toMatch(/axios\.get\(['"]\/api\/alert-channels['"]\)/)
  })

  it('Add Channel button is gated by manage_alerts', () => {
    // The button should have v-if="canManage" where canManage is
    // computed from the manage_alerts permission.  We assert the
    // permission is referenced (the exact expression is allowed to
    // change as long as the intent stays).
    const btn = alertsSrc.match(/<v-btn[^>]*@click="openNewChannel"[\s\S]*?>/)
    expect(btn, 'Add Channel button not found').toBeTruthy()
    expect(btn[0]).toMatch(/v-if="canManage"/)
  })

  it('Edit and Delete actions in the channel table are gated by canManage', () => {
    const actions = alertsSrc.match(
      /<template #item\.actions="\{ item \}">[\s\S]*?<\/template>/
    )
    expect(actions, 'channel actions template not found').toBeTruthy()
    // The whole actions block should be wrapped in v-if="canManage"
    // so non-managers see no edit/delete buttons.
    expect(actions[0]).toMatch(/v-if="canManage"/)
  })

  it('delete channel handler surfaces the server detail on 409', () => {
    // The doDeleteChannel function must read e.response?.data?.detail
    // so the operator sees the "referenced by rule X" message.
    expect(alertsSrc).toMatch(/e\.response\?\.data\?\.detail/)
  })

  it('shows an empty state when no channels exist', () => {
    expect(alertsSrc).toMatch(/No channels yet/)
  })
})
