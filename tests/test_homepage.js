'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../admin/static/app.js'), 'utf8');
const bootstrap = source.lastIndexOf('\n(async () =>');
assert.ok(bootstrap > 0, 'Locate browser bootstrap before evaluating renderers');
const context = vm.createContext({document:{querySelector:()=>({})},setInterval:()=>0,URL});
vm.runInContext(source.slice(0,bootstrap), context);
vm.runInContext(`
state.status = {setupComplete:true, settings:{
  management:{address:'192.0.2.10'}, apps:{enable:true,portainerPort:9443}, cockpit:{enable:true,port:9090}, monitoring:{history:false},
  tunnel:{enable:true,ingress:{'photos.example.com':'http://10.77.0.10:8080','*.example.com':'http://10.77.0.10:8080'}}
}};
state.draft = {management:{address:'198.51.100.20'}, apps:{enable:false}};
`, context);
let tools = vm.runInContext('homepageTools()', context);
assert.ok(tools.includes('href="https://192.0.2.10:8443"'));
assert.ok(tools.includes('href="https://192.0.2.10:9443"'));
assert.ok(tools.includes('href="https://192.0.2.10:9090"'));
assert.ok(!tools.includes('198.51.100.20'), 'Unsaved addresses must not become management links');
assert.ok(!tools.includes('href="http://192.0.2.10:9090"'), 'Loopback monitoring must not get a misleading LAN link');
vm.runInContext('state.status.settings.apps.enable=false;state.status.settings.cockpit.enable=false', context);
tools = vm.runInContext('homepageTools()', context);
assert.ok(!tools.includes(':9443"'));
assert.ok(!tools.includes(':9090"'));
assert.ok(tools.includes('Enable apps'));
vm.runInContext('state.status.setupComplete=false', context);
tools = vm.runInContext('homepageTools()', context);
assert.ok(!tools.includes(':8443"'));
assert.ok(tools.includes('Finish setup'));
let resources = vm.runInContext('homepageResources()', context);
for (const destination of ['https://github.com/community-scripts/Incus','https://community-scripts.org/','https://github.com/community-scripts/ProxmoxVE','https://photos.example.com','roadmap.md','recovery.md']) {
  assert.ok(resources.includes(destination), 'Missing destination: '+destination);
}
assert.ok(!resources.includes('https://*.example.com'), 'Wildcard DNS records are not browsable endpoints');
assert.ok(resources.includes('never execute installers'));
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
