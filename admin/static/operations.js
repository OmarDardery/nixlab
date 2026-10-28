'use strict';
let operationView = {inventory:null, jobs:[], archives:[], health:null, users:[], recipes:[], loading:false, error:''};
const administrator = () => (state.principal?.role || 'admin') === 'admin';

function operationsPage(){
  const permitted=(state.principal?.role||'admin')!=='viewer';
  const busy=operationView.jobs.some(j=>['queued','running'].includes(j.status));
  const guests=operationView.inventory||[];
  return title('DAILY OPERATIONS','Guests, backups and recovery.','Operations run in independent jobs. Restores create stopped copies; existing instances are never overwritten.')+
    card('Guest inventory',`<button data-ops-refresh>Refresh inventory</button>${operationView.error?`<p class="error">${esc(operationView.error)}</p>`:''}<div class="table-wrap"><table><thead><tr><th>Guest</th><th>State & addresses</th><th>Resources & ownership</th><th>Operations</th></tr></thead><tbody>${guests.map(g=>`<tr><td>${esc(g.name)}<br><small>${esc(g.project)} · ${esc(g.type)}</small></td><td>${esc(g.status)}<br>${esc(g.addresses.join(', '))}${g.restoreReview?'<br><b>Restore review required</b>':''}</td><td>${esc(g.cpu||'Profile default')} CPU · ${esc(g.memory||'Profile default')}<br>${g.managed?'Nix-managed fields':'Incus-managed'}${g.drift?`<br><span class="error">${esc(g.drift)}</span>`:''}</td><td>${permitted?['start','stop','restart','snapshot','export'].map(action=>`<button class="small" data-guest-action="${action}" data-guest="${esc(g.name)}" ${busy?'disabled':''}>${esc(action)}</button>`).join(''):''}${administrator()&&g.restoreReview?`<button class="small" data-guest-action="release-restore" data-guest="${esc(g.name)}" ${busy?'disabled':''}>Release reviewed restore</button>`:''}</td></tr>`).join('')||'<tr><td colspan="4">No guests loaded. Complete setup or refresh inventory.</td></tr>'}</tbody></table></div><p class="note">Stop/restart/export can interrupt workloads. Instance exports exclude attached custom-volume contents. Nix-managed fields should be changed in their declaration.</p>`)+
    recipeCard()+card('Restore into a new instance',`<div class="fields"><label class="field"><span>Completed export</span><select id="restore-archive"><option value="">Select an export</option>${operationView.archives.map(a=>`<option value="${esc(a.id)}" ${a.available===false?'disabled':''}>${esc(a.name)}${a.available===false?' (storage unavailable)':''} · ${formatBytes(a.size)} · ${esc(new Date(a.created*1000).toLocaleString())}</option>`).join('')}</select></label><label class="field"><span>New instance name</span><input id="restore-target" placeholder="restored-test"></label></div><button data-restore-export ${!administrator()||busy?'disabled':''}>Restore stopped copy</button><div class="separator"></div><div class="fields"><label class="field"><span>Source guest</span><select id="snapshot-guest">${guests.map(g=>`<option value="${esc(g.name)}">${esc(g.name)}</option>`).join('')}</select></label><label class="field"><span>Existing snapshot name</span><input id="snapshot-name"></label><label class="field"><span>New instance name</span><input id="snapshot-target" placeholder="snapshot-test"></label></div><button data-restore-snapshot ${!administrator()||busy?'disabled':''}>Copy snapshot to stopped guest</button><p class="note">Autostart is disabled on restored copies. Review NIC addresses, attached volumes and credentials in Incus before releasing and starting them. Shared custom volumes need an independent restore plan.</p>`)+
    card('Independent backup',`<p>Scheduled restic backup exports guests and custom volumes, configuration and runtime identities. It requires the independent destination configured on Applications.</p><button data-run-backup ${!administrator()||busy||!state.status.settings.backup.enable?'disabled':''}>Run configured backup now</button><p>Last success: ${operationView.health?.lastBackupSuccess?esc(new Date(operationView.health.lastBackupSuccess*1000).toLocaleString()):'No success recorded'}</p><p>Guests awaiting restart recovery: ${esc(operationView.health?.guestsAwaitingRestart??'Unknown')}</p>`)+
    card('Operation history',`<div class="table-wrap"><table><thead><tr><th>Action</th><th>Instance</th><th>Status</th><th>Result</th></tr></thead><tbody>${operationView.jobs.map(j=>`<tr><td>${esc(j.action)}</td><td>${esc(j.project)}/${esc(j.name)}</td><td>${esc(j.status)}</td><td>${esc(j.error||j.restoredInstance||j.sha256||'')}</td></tr>`).join('')}</tbody></table></div>`)+
    card('Service and certificate health',`<pre class="log">${esc(JSON.stringify(operationView.health,null,2))}</pre><button data-download-diagnostics>Download diagnostics</button><p class="note">This report contains version, service states and certificate expiry. It excludes arbitrary journal output and stored secret contents.</p>`)+
    (administrator()?card('Portable recovery kit',`<p>Keep configuration and encrypted credentials off this host. VM disks and database backups remain separate. Nix source is included as written; avoid inline credentials.</p><div class="fields"><label class="field full"><span>age public recipient (optional; includes separately encrypted secrets)</span><input id="recovery-recipient" placeholder="age1…"></label><label class="field"><span>Your password</span><input id="recovery-password" type="password" autocomplete="current-password"></label></div><button data-download-recovery>Download recovery kit</button>`)+accountCard():'');
}

function accountCard(){return card('Portal accounts',`<ul>${operationView.users.map(u=>`<li>${esc(u.username)} · ${esc(u.role)}</li>`).join('')}</ul><p>Administrators control the host. Operators can run permitted guest actions in lab. Viewers can inspect status. SSH/sudo and Incus certificates have their own permissions.</p><div class="fields"><label class="field"><span>Account name</span><input id="account-name" autocomplete="off"></label><label class="field"><span>Role</span><select id="account-role"><option>viewer</option><option>operator</option><option>admin</option></select></label><label class="field"><span>New account password (blank keeps an existing password)</span><input id="account-password" type="password" autocomplete="new-password"></label><label class="field"><span>Your password</span><input id="account-current" type="password" autocomplete="current-password"></label></div><button data-account-save>Save account</button> <button class="danger" data-account-delete>Delete named account</button>`);}

function platformReview(){
  if(!administrator())return '';
  const job=state.status.job;
  return card('Platform source upgrade',`<p>Installed revision: <code>${esc(state.status.release?.revision||'unknown')}</code>. Use a full reviewed commit from the Nixlab repository. Local machine settings and modules are preserved.</p><label class="field full"><span>Upstream commit SHA (optional)</span><input id="platform-revision" value="${esc(state.platformRevision||'')}" maxlength="40" placeholder="40 hexadecimal characters"></label><div class="fields"><label class="field"><span>Your password for code changes</span><input id="platform-password" type="password" autocomplete="current-password"></label></div><label class="toggle"><input id="platform-ack" type="checkbox">I reviewed this privileged platform revision and approve building its system code.</label>${job?.platformRevision?`<p>Candidate: <a href="${projectRepository}/commit/${esc(job.platformRevision)}" target="_blank" rel="noopener noreferrer">${esc(job.platformRevision)}</a></p><pre class="log">${esc(JSON.stringify(job.platformChanges||[],null,2))}</pre>`:''}${job?.inputChanges?`<p>Changed pinned inputs:</p><pre class="log">${esc(JSON.stringify(job.inputChanges,null,2))}</pre>`:''}${job?.rebootRequired?'<p class="note">The candidate kernel differs from the booted kernel. Plan an explicit reboot after confirming activation.</p>':''}`);
}

async function refreshOperations(full=true){
  if(operationView.loading)return;
  operationView.loading=true;
  try{
    const operations=await api('operations');
    const changed=JSON.stringify(operations.jobs)!==JSON.stringify(operationView.jobs);
    Object.assign(operationView,operations);
    if(full||changed){
      operationView.inventory=(await api('guests')).guests;
      operationView.health=await api('health');
      operationView.recipes=(await api('recipes')).recipes;
      if(administrator())operationView.users=(await api('users')).users;
    }
    operationView.error='';
  }catch(error){operationView.error=error.message;operationView.inventory??=[];}
  finally{operationView.loading=false;if(state.page==='operations')shell();}
}

async function submitOperation(request){await api('operation',request);await refreshOperations();}
function bindOperations(){
  const safe=fn=>async event=>{const el=event.currentTarget;el.disabled=true;try{await fn(el);}catch(error){toast(error.message);}finally{if(el.isConnected)el.disabled=false;}};
  document.querySelectorAll('[data-ops-refresh]').forEach(el=>el.onclick=safe(()=>refreshOperations()));
  document.querySelectorAll('[data-guest-action]').forEach(el=>el.onclick=safe(async button=>{
    const action=button.dataset.guestAction,name=button.dataset.guest;
    let snapshot;
    if(action==='snapshot'){snapshot=window.prompt('New snapshot name:');if(!snapshot)return;}
    const acknowledge=action==='start'||action==='snapshot'||window.confirm(`${action} ${name}? This can interrupt the guest. Restored guests require reviewed networking and volumes.`);
    if(!acknowledge)return;
    await submitOperation({action,name,project:'lab',...(snapshot?{snapshot}:{}),acknowledge});
  }));
  document.querySelectorAll('[data-recipe]').forEach(el=>el.onclick=safe(async button=>{
    const recipe=operationView.recipes.find(r=>r.id===button.dataset.recipe);
    if(recipe.module)state.modules['recipes/'+recipe.id+'.nix']=recipe.module;
    function patch(target,changes){for(const [key,value] of Object.entries(changes)){if(value&&typeof value==='object'&&!Array.isArray(value))patch(target[key],value);else target[key]=value;}}
    if(recipe.settings)patch(state.draft,recipe.settings);
    state.page=recipe.settings?.apps?'apps':'review';shell();toast('Recipe staged. Review settings and required credentials before building.');
  }));
  const by=id=>document.getElementById(id);
  const restore=by('restore-archive');
  if(restore)document.querySelector('[data-restore-export]').onclick=safe(async()=>{
    const archive=operationView.archives.find(a=>a.id===restore.value);
    if(!archive)throw Error('Select an export.');
    if(window.confirm('Restore to a new stopped instance? Review networking and attached volumes before starting.'))await submitOperation({action:'restore',project:archive.project,name:archive.name,archive:archive.id,target:by('restore-target').value.trim(),acknowledge:true});
  });
  document.querySelectorAll('[data-restore-snapshot]').forEach(el=>el.onclick=safe(async()=>{
    if(window.confirm('Create a stopped copy of this snapshot?'))await submitOperation({action:'snapshot-copy',project:'lab',name:by('snapshot-guest').value,snapshot:by('snapshot-name').value.trim(),target:by('snapshot-target').value.trim(),acknowledge:true});
  }));
  document.querySelectorAll('[data-run-backup]').forEach(el=>el.onclick=safe(async()=>{
    if(window.confirm('Run the configured independent backup? Guests stop gracefully for exports, then restart.'))await submitOperation({action:'backup',project:'lab',name:'all',acknowledge:true});
  }));
  document.querySelectorAll('[data-download-diagnostics]').forEach(el=>el.onclick=()=>download('nixlab-diagnostics.json',JSON.stringify(operationView.health,null,2),'application/json'));
  document.querySelectorAll('[data-download-recovery]').forEach(el=>el.onclick=safe(async()=>{
    const result=await api('recovery-export',{recipient:by('recovery-recipient').value.trim()||null,current:by('recovery-password').value});
    for(const [name,data] of Object.entries(result.files))download(name,Uint8Array.from(atob(data),c=>c.charCodeAt(0)),'application/octet-stream');
    by('recovery-password').value='';
  }));
  for(const [selector,remove] of [['[data-account-save]',false],['[data-account-delete]',true]])document.querySelectorAll(selector).forEach(el=>el.onclick=safe(async()=>{
    if(remove&&!window.confirm('Delete this named account and revoke its portal sessions?'))return;
    operationView.users=(await api('users',{username:by('account-name').value.trim(),role:by('account-role').value,password:by('account-password').value,current:by('account-current').value,delete:remove})).users;
    shell();
  }));
  const revision=by('platform-revision');if(revision)revision.oninput=()=>state.platformRevision=revision.value.trim();
  if(state.page==='operations'&&operationView.inventory===null&&!operationView.loading)refreshOperations();
  if(!administrator()){
    document.querySelectorAll('[data-path],[data-secret],[data-mount],[data-remove-mount],[data-ingress]').forEach(el=>el.disabled=true);
    document.querySelectorAll('[data-action]').forEach(el=>{if(!['logout','copy-ssh','export-settings'].includes(el.dataset.action))el.disabled=true;});
  }
}

function recipeCard(){return card('Reviewed recipes',`<p>Stage a packaged recipe, inspect its settings or source, then use the normal build and confirmation workflow. Optional services remain off until activated.</p>${operationView.recipes.map(r=>`<div class="service-row"><div class="service-copy"><h3>${esc(r.title)}</h3><p>${esc(r.description)}</p></div><button data-recipe="${esc(r.id)}" ${administrator()?'':'disabled'}>Stage recipe</button></div>`).join('')}`);}
