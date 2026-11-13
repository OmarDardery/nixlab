'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../admin/static/app.js'), 'utf8');
const bootstrap = source.lastIndexOf('\n(async () =>');
assert.ok(bootstrap > 0, 'Locate browser bootstrap before evaluating renderers');
const context = vm.createContext({document:{querySelector:()=>({})},setInterval:()=>0,URL});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../admin/static/projects.js'),'utf8'),context);
for (const name of ['foundation.js', 'operations.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../admin/static',name),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../admin/static/commands.js'), 'utf8'), context);
vm.runInContext(source.slice(0,bootstrap), context);
vm.runInContext(`
state.status = {setupComplete:true, settings:{
  management:{address:'192.0.2.10'}, apps:{enable:true,portainerPort:9443}, cockpit:{enable:true,port:9090}, monitoring:{history:false},
  tunnel:{enable:true,ingress:{'photos.example.com':'http://10.77.0.10:8080','*.example.com':'http://10.77.0.10:8080'}}
}};
state.draft = {management:{address:'198.51.100.20'}, apps:{enable:false}};
`, context);
let shortcuts = vm.runInContext('quickManagementLinks(state.status.settings)', context);
for (const url of ['https://192.0.2.10:8443','https://192.0.2.10:9090','https://192.0.2.10:9443']) {
  assert.ok(shortcuts.includes('href="' + url + '"'), 'Missing sidebar shortcut: ' + url);
}
assert.equal(vm.runInContext("pages.find(([id]) => id === 'commands')[1]",context), 'Help', 'Docs remain one primary navigation step away');
assert.ok(shortcuts.includes('rel="noopener noreferrer"'), 'External tools must not retain opener access');
let tools = vm.runInContext('homepageTools()', context);
assert.ok(tools.includes('data-go="integrations"'), 'Overview must expose native service settings');
assert.ok(tools.includes('href="https://192.0.2.10:8443"'));
assert.ok(tools.includes('href="https://192.0.2.10:9443"'));
assert.ok(tools.includes('href="https://192.0.2.10:9090"'));
assert.ok(!tools.includes('198.51.100.20'), 'Unsaved addresses must not become management links');
assert.ok(!tools.includes('href="http://192.0.2.10:9090"'), 'Loopback monitoring must not get a misleading LAN link');
vm.runInContext('state.status.settings.apps.enable=false;state.status.settings.cockpit.enable=false', context);
tools = vm.runInContext('homepageTools()', context);
assert.ok(!tools.includes(':9443"'));
assert.ok(!tools.includes(':9090"'));
shortcuts = vm.runInContext('quickManagementLinks(state.status.settings)', context);
assert.ok(!shortcuts.includes(':9443"'));
assert.ok(!shortcuts.includes(':9090"'));
assert.ok(tools.includes('Service settings'));
vm.runInContext('state.status.setupComplete=false', context);
tools = vm.runInContext('homepageTools()', context);
assert.ok(!tools.includes(':8443"'));
assert.ok(tools.includes('Finish setup'));
assert.equal(vm.runInContext('quickManagementLinks(state.status.settings)', context), '', 'No unverified management shortcuts before setup');
let resources = vm.runInContext('homepageResources()', context);
for (const destination of ['https://github.com/community-scripts/Incus','https://community-scripts.org/','https://github.com/docker/awesome-compose','https://github.com/lissy93/portainer-templates','https://photos.example.com','roadmap.md','recovery.md']) {
  assert.ok(resources.includes(destination), 'Missing destination: '+destination);
}
assert.ok(!resources.includes('https://*.example.com'), 'Wildcard DNS records are not browsable endpoints');
assert.ok(!/data-(action|install-script|guest-action)=/.test(resources), 'Reference links must not execute host operations');
for (const match of (tools+resources).matchAll(/<a [^>]+>/g)) {
  assert.ok(match[0].includes('rel="noopener noreferrer"'), 'External tabs must not retain opener/referrer access');
}
vm.runInContext('state.status.settings.tunnel.enable=false', context);
resources = vm.runInContext('homepageResources()', context);
assert.ok(!resources.includes('https://photos.example.com'));
console.log('Homepage checks passed: active URLs, disabled services, helper resources and public endpoints.');

vm.runInContext("state.status.job={moduleChanges:[{path:'<script>',before:null,after:'<img src=x onerror=alert(1)>'}]};",context);
const moduleReview = vm.runInContext('moduleReview()',context);
assert.ok(moduleReview.includes('&lt;script&gt;'));
assert.ok(!moduleReview.includes('<img src=x'));

vm.runInContext("state.status.setupComplete=true; state.status.effectiveSettings=JSON.parse(JSON.stringify(state.status.settings)); state.status.effectiveSettings.management.address='198.51.100.30';",context);
const effectiveTools=vm.runInContext('homepageTools()',context);
assert.ok(effectiveTools.includes('https://198.51.100.30:8443'));
assert.ok(!effectiveTools.includes('https://192.0.2.10:8443'));
vm.runInContext("state.status.managementAddress='198.51.100.40'", context);
const observedTools=vm.runInContext('homepageTools()',context);
assert.ok(observedTools.includes('https://198.51.100.40:8443'));
assert.ok(!observedTools.includes('https://198.51.100.30:8443'));
vm.runInContext("state.status.codeOverrides=[{setting:'cockpit.enable'}];state.draft.cockpit={enable:false};state.status.effectiveSettings.cockpit.enable=true;", context);
assert.ok(vm.runInContext("toggle('Cockpit','cockpit.enable')", context).includes('checked'));
assert.equal(vm.runInContext('state.draft.cockpit.enable', context), false, 'Displaying file-owned values must not change the JSON overlay');

vm.runInContext(`
state.host={kvm:true};
operationView.network={available:true, checkedAt:Date.now()/1000, conflicts:[
  {project:'default',network:'private',address:'10.77.0.5',owners:['lab/a','<img src=x>']}], guests:[
  {name:'<script>bad</script>', project:'another-project', type:'virtual-machine', status:'Running', observed:['10.77.0.5'],
   nics:[{name:'eth0',network:'private',observed:['10.77.0.5'],reserved:['10.77.0.9'],unobservedReservation:true}]}]};
`, context);
let network = vm.runInContext('guestNetwork()', context);
for (const expected of ['another-project', '10.77.0.5', '10.77.0.9', 'Potential address conflict', 'Reservation not observed']) assert.ok(network.includes(expected));
assert.ok(network.includes('&lt;script&gt;bad&lt;/script&gt;'));
assert.ok(!network.includes('<script>bad') && !network.includes('<img src=x>'));
assert.ok(!network.includes('href="http://10.77.0.5') && !network.includes('data-guest-action'), 'Observed addresses must not invent service endpoints or duplicate lifecycle controls');
vm.runInContext('operationView.network.checkedAt -= 100', context);
assert.ok(vm.runInContext('guestNetwork()', context).includes('Inventory is stale'));
vm.runInContext("operationView.networkError='Incus unavailable'", context);
assert.ok(vm.runInContext('guestNetwork()', context).includes('Incus unavailable'));
vm.runInContext('operationView.network=null', context);
assert.ok(!vm.runInContext('guestNetwork()', context).includes('No guests reported'), 'Failed observations are not an empty healthy inventory');
vm.runInContext(`operationView.health={checkedAt:Date.now()/1000,services:[
  {Id:'incus.service',LoadState:'loaded',ActiveState:'inactive'}]}`, context);
assert.ok(vm.runInContext('hostOverview()', context).includes('Needs attention'));
vm.runInContext('operationView.health.checkedAt -= 100', context);
assert.ok(vm.runInContext('hostOverview()', context).includes('Unavailable or stale'));
vm.runInContext("state.status.managementAddress='user:password@evil.example'", context);
assert.equal(vm.runInContext('quickManagementLinks()', context), '', 'Never turn management metadata into credential-bearing or foreign URLs');
vm.runInContext("state.status.managementAddress='2001:db8::10'", context);
assert.ok(vm.runInContext('quickManagementLinks()', context).includes('https://[2001:db8::10]:8443'));

