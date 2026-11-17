'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const context = vm.createContext({state: {status: {}}, administrator: () => true, card: (title, body) => title + body,
  esc: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')});
vm.runInContext(fs.readFileSync('admin/static/foundation.js', 'utf8'), context);
assert.equal(vm.runInContext('guestDeploymentReview()', context), '');
context.state.status.job = {status: 'ready', guestPlan: {boundary: 'OS rollback excludes database schemas', targets: [
  {id: 'apps', project: 'lab', name: '<img src=x>', targetSystem: '/nix/store/target', currentSystem: '/nix/store/old',
   dependsOn: [], maxAttempts: 3, restartPolicy: 'defer', healthUnits: ['example.service'], httpChecks: 1} ]}};
let html = vm.runInContext('guestDeploymentReview()', context);
assert.ok(html.includes('&lt;img src=x&gt;') && !html.includes('<img src=x>'));
assert.ok(html.includes('data-action="deploy-guests" disabled'));
assert.ok(html.includes('schemas'));
context.state.status.job.status = 'confirmed';
assert.ok(vm.runInContext('guestDeploymentReview()', context).includes('data-action="deploy-guests" >'));
context.state.status.job.guestDeployment = {status: 'running', targets: {apps: {phase: 'transferring', attempts: 1}}};
assert.ok(vm.runInContext('guestDeploymentReview()', context).includes('data-action="deploy-guests" disabled'));
context.state.status.job.guestDeployment = {status: 'partial', targets: {apps: {phase: 'reboot-pending', attempts: 1, currentSystem: '/nix/store/active', error: '<script>bad</script>'}}};
html = vm.runInContext('guestDeploymentReview()', context);
assert.ok(html.includes('reboot-pending') && html.includes('Resume / recheck guests') && html.includes('/nix/store/active'));
assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));
context.administrator = () => false;
assert.ok(vm.runInContext('guestDeploymentReview()', context).includes('data-action="deploy-guests" disabled'));
console.log('Guest deployment UI checks passed: confirmation, roles, phases, current generations and escaping.');
