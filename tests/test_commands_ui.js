'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../admin/static');
const context = vm.createContext({document: {querySelector: () => ({})}, setInterval: () => 0, setTimeout: () => 0, URL});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../admin/static/projects.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root, 'commands.js'), 'utf8'), context);
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
vm.runInContext(app.slice(0, app.lastIndexOf('\n(async () =>')), context);
context.fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/defaults.json'), 'utf8'));
vm.runInContext(`state.page='commands'; state.status={settings:fixture,managementAddress:'192.0.2.10'};`, context);
const groups = vm.runInContext('commandGroups()', context);
const items = groups.flatMap(group => group.items);
assert.ok(!/klipper|aiostreams/i.test(JSON.stringify(items)), 'Cheatsheet must not hardcode example applications');
assert.equal(new Set(items.map(item => item[0])).size, items.length, 'Every copy target needs a unique ID');
assert.ok(items.some(item => item[0] === 'platform-update' && item[4] === 'nix run --refresh github:OmarDardery/nixlab#update'));
assert.ok(items.some(item => item[4] === 'sudo nixlab-admin config review --update-inputs'));
assert.ok(items.some(item => item[4] === 'sudo nixlab-helpers run debian --project lab'));
assert.ok(items.some(item => item[4] === 'sudo homelab exec apps -- docker compose ls'));
assert.ok(items.some(item => item[4] === 'ssh operator@192.0.2.10'));
vm.runInContext(`state.status.managementAddress='x; touch /tmp/unwanted';`, context);
assert.equal(vm.runInContext("commandGroups().find(group => group.id === 'health').items.find(item => item[0] === 'ssh')[4]", context), 'ssh operator@HOST_IP', 'Never interpolate shell metacharacters from host metadata');
const html = vm.runInContext('commandsPage()', context);
assert.ok(!/data-action=|data-guest-action=|data-install-script=/.test(html), 'Command reference must have no execution or mutation controls');
assert.equal((html.match(/data-copy-command=/g) || []).length, items.length);
for (const target of ['community-scripts/Incus', 'cockpit-project.org/applications', 'docker/awesome-compose', 'lissy93/portainer-templates', 'restic/others', 'Mic92/sops-nix']) {
  assert.ok(html.includes(target), 'Missing project resource: ' + target);
}
for (const link of html.matchAll(/<a [^>]+>/g)) {
  if (link[0].includes('target="_blank"')) assert.ok(link[0].includes('rel="noopener noreferrer"'));
}

async function main() {
  const copied = [];
  const button = {dataset: {copyCommand: 'doctor'}, textContent: 'Copy'};
  context.document.querySelectorAll = () => [button];
  context.document.getElementById = id => { assert.equal(id, 'command-doctor'); return {textContent: 'sudo nixlab doctor'}; };
  context.navigator = {clipboard: {writeText: async text => { copied.push(text); }}};
  let backendCalls = 0;
  context.api = () => { backendCalls++; throw new Error('No host operations allowed'); };
  vm.runInContext('toast = () => {}; bindCommands();', context);
  await button.onclick();
  assert.deepEqual(copied, ['sudo nixlab doctor']);
  assert.equal(button.textContent, 'Copied');
  let selected = false;
  context.navigator.clipboard.writeText = async () => { throw new Error('Clipboard unavailable'); };
  context.document.createRange = () => ({selectNodeContents: () => { selected = true; }});
  context.window = {getSelection: () => ({removeAllRanges() {}, addRange() {}})};
  await button.onclick();
  assert.ok(selected, 'Clipboard failure must leave a manual selection');
  assert.equal(button.textContent, 'Selected');
  assert.equal(backendCalls, 0);
  console.log('Commands UI checks passed: examples, project references, safe copy/fallback and no host execution.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
