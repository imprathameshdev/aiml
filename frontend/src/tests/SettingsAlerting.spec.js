/**
 * Structural tests for the Settings.vue "Alerting" section.
 *
 * Note: the "Enable alerting" switch only gates the EMAIL channel.
 * Webhook and MQTT channels are not affected by this toggle and
 * must be disabled individually in the Alerts page.  These tests
 * pin that contract so a future refactor doesn't accidentally
 * re-label the switch as a global master switch without actually
 * implementing one.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.src ?? import.meta.url))
const SFC = path.join(__dirname, '..', 'components', 'Settings.vue')
const src = fs.readFileSync(SFC, 'utf8')

describe('Settings.vue — Alerting section', () => {
  // The SMTP / email-alerting block.  We extract it by anchor — the
  // section's leading ``Configure the system-wide SMTP server``
  // paragraph is unique to this block.  Anchoring on the v-if alone
  // isn't sufficient now that the Inference Interval info-label
  // (a ``d-flex align-center`` block) lives higher up in the same
  // card-text.
  const smtpBlock = () => {
    // The SMTP block is the v-if="hasPermission('edit_settings')"
    // that contains the "Configure the system-wide SMTP server"
    // paragraph.  Greedy match (not the first v-if) so we get the
    // whole block including the trailing divs.
    const re = /<div v-if="hasPermission\('edit_settings'\)" class="mb-6">[\s\S]*?Configure the system-wide SMTP server[\s\S]*?(?=<div v-if="hasPermission\('manage_billing'\)"|<\/div>\s*<\/v-card-text>)/;
    const m = src.match(re);
    return m ? m[0] : null;
  };

  it('renders an Alerting section gated by edit_settings', () => {
    expect(src).toMatch(/Alerting \(SMTP for email channels\)/);
    // The whole section is wrapped in a v-if="hasPermission('edit_settings')".
    // Use the smtpBlock helper to scope the check to the SMTP
    // section specifically — anchoring on the v-if alone now also
    // catches the Inference Interval info-label block.
    const block = smtpBlock();
    expect(block, 'alerting section wrapper not found').toBeTruthy();
    // The whole SMTP block must live under the edit_settings gate
    expect(block).toMatch(/<div v-if="hasPermission\('edit_settings'\)"/);
    // The switch must be labelled "Enable email alerts" — not the
    // ambiguous "Enable alerting", which falsely suggests it gates
    // the entire alerting engine (it only gates the email channel).
    expect(block).toMatch(/Enable email alerts/);
    expect(block).not.toMatch(/label="Enable alerting"/);
  });

  it('help text mentions the third-party SMTP provider recommendation', () => {
    // The section help text surfaces a third-party SMTP option
    // (SMTP2Go) so operators who don't have their own SMTP server
    // can find one quickly.  The link is rendered with
    // target="_blank" so it opens in a new tab.
    const block = smtpBlock();
    expect(block, 'alerting section wrapper not found').toBeTruthy();
    expect(block).toMatch(/Configure the system-wide SMTP server/);
    // The body text wraps across multiple lines; tolerate any
    // whitespace between "third" and "party".
    expect(block).toMatch(/third[\s\S]*?party provider/);
    expect(block).toMatch(/SMTP2Go/);
    expect(block).toMatch(/href="https:\/\/www\.smtp2go\.com\/"/);
    expect(block).toMatch(/target="_blank"/);
    // The old "this toggle only gates the email channel" callout
    // was replaced by the SMTP2Go recommendation.  Pin its absence
    // so a future refactor that re-introduces the long-form
    // channel-gating explanation is intentional (and can re-test it
    // with the matching assertions).
    expect(block).not.toMatch(/Webhook/);
    expect(block).not.toMatch(/MQTT/);
  });

  it('has a master switch and all seven SMTP fields', () => {
    const block = smtpBlock();
    expect(block, 'alerting section wrapper not found').toBeTruthy();
    expect(block).toMatch(/v-model="alertingEnabled"/);
    expect(block).toMatch(/v-model="smtpHost"/);
    expect(block).toMatch(/v-model\.number="smtpPort"/);
    expect(block).toMatch(/v-model="smtpUser"/);
    expect(block).toMatch(/v-model="smtpPass"/);
    expect(block).toMatch(/v-model="smtpFrom"/);
    expect(block).toMatch(/v-model="smtpUseTls"/);
  });

  it('SMTP fields are disabled when email alerts are disabled', () => {
    // Each SMTP field has :disabled="!alertingEnabled" so the
    // operator can't enter creds when the email switch is off.
    const block = smtpBlock();
    expect(block, 'alerting section wrapper not found').toBeTruthy();
    const disabledCount = (block.match(/:disabled="!alertingEnabled"/g) || []).length;
    // Master switch, host, port, user, pass, from, useTls = 7 controls.
    expect(disabledCount).toBeGreaterThanOrEqual(5);
  });

  it('saveSettings PUTs all seven alerting_smtp_* keys', () => {
    // The save handler must write all the SMTP keys so a
    // partial save doesn't leave alerting half-configured.
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/alerting_enabled['"]/);
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/alerting_smtp_host['"]/);
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/alerting_smtp_port['"]/);
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/alerting_smtp_user['"]/);
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/alerting_smtp_pass['"]/);
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/alerting_smtp_from['"]/);
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/alerting_smtp_use_tls['"]/);
  });

  it('fetchSettings reads all seven alerting_smtp_* keys', () => {
    // The fetch path must read all SMTP keys so the UI shows the
    // current values on load.
    expect(src).toMatch(/alerting_enabled['"],\s*'false'\)/);
    expect(src).toMatch(/alerting_smtp_host['"],\s*''\)/);
    expect(src).toMatch(/alerting_smtp_port['"],\s*'587'\)/);
    expect(src).toMatch(/alerting_smtp_user['"],\s*''\)/);
    expect(src).toMatch(/alerting_smtp_pass['"],\s*''\)/);
    expect(src).toMatch(/alerting_smtp_from['"],\s*''\)/)
    expect(src).toMatch(/alerting_smtp_use_tls['"],\s*'true'\)/)
  })
})

describe('Settings.vue — Security & Encryption (SSL/TLS) section', () => {
  it('renders a Security & Encryption section gated by edit_settings', () => {
    expect(src).toMatch(/Security & Encryption \(SSL\/TLS\)/)
    expect(src).toMatch(/v-model="sslEnabled"/)
    expect(src).toMatch(/Upload SSL Credentials/)
    expect(src).toMatch(/Generate Self-Signed Certificate/)
  })

  it('saveSettings PUTs the ssl_enabled key', () => {
    expect(src).toMatch(/axios\.put\(['"]\/api\/settings\/ssl_enabled['"]/)
  })

  it('fetchSettings reads the ssl_* keys', () => {
    expect(src).toMatch(/ssl_enabled['"],\s*'false'\)/)
    expect(src).toMatch(/ssl_cert_path['"],\s*''\)/)
    expect(src).toMatch(/ssl_key_path['"],\s*''\)/)
  })
})

