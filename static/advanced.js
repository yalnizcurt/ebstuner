'use strict';
let st=null, pid=null, active='workspace', current=1, pageNumber=1, gridPage=1, grid=null, draftAllowed=false;
let selectedRows=new Set(), cellBusy=false, repairOffset=0, repairTotal=0;
let samples=[], aiStatus=null;
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dot=s=>String(s??'').replace(/ /g,'·').replace(/\t/g,'→');
const titles={workspace:'Workspace',schema:'CTL schema explorer',diagnostics:'DAT diagnostics',repairs:'Repair workspace',editor:'Spreadsheet data editor',bytes:'Byte inspector',export:'Generate & export',ai:'AI connection'};
function show(message,error=false){const e=$('#notice');e.className='alert toast '+(error?'error':'success');e.textContent=typeof message==='string'?message:JSON.stringify(message)}
function confirmAction(message){
  return new Promise(resolve=>{const dialog=$('#confirmDialog');$('#confirmMessage').textContent=message;dialog.returnValue='cancel';dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.showModal()});
}
function fail(e){show(e.message||String(e),true)}
async function request(path,options={}){
  const r=await fetch(path,{...options,headers:{'X-Studio-Request':'1',...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers}});
  if(!r.ok){let d;try{d=await r.json()}catch{};if(r.status===409&&pid) refresh().catch(()=>{});throw Error(typeof d?.detail==='string'?d.detail:JSON.stringify(d?.detail||`HTTP ${r.status}`))}
  return r.json();
}
const api=path=>`/api/projects/${encodeURIComponent(pid)}${path}`;
function requireProject(){if(!pid||!st?.loaded)throw Error('Import a CTL and DAT into a project first')}
async function mutate(path,body,method='POST'){requireProject();st=await request(api(path),{method,body:JSON.stringify({version:st.project.version,...body})});render();if(active==='repairs')await loadProposals();return st}
function switchPage(name){if(!st?.loaded&&!['workspace','ai'].includes(name)){show('Choose a practice case or import your CTL + DAT to begin.');name='workspace'}active=name;$$('.nav').forEach(x=>x.classList.toggle('active',x.dataset.page===name));$$('.section').forEach(x=>x.classList.toggle('active',x.id===name));$('#pageTitle').textContent=titles[name];if(name==='editor'&&st?.loaded)loadGrid().catch(fail);if(name==='repairs'&&st?.loaded)loadProposals().catch(fail);if(name==='ai')loadAIStatus().catch(fail);window.scrollTo(0,0)}
async function refresh(n=current){if(!pid)return;const q=new URLSearchParams({record:n,page:pageNumber,page_size:50,search:$('#recordSearch').value,record_type:$('#typeFilter').value,status:$('#statusFilter').value,sort:$('#sortFilter').value});st=await request(api('/state?'+q));current=st.record_num||1;pageNumber=st.pagination?.page||1;render()}
function render(){
  $('#projectName').textContent=st?.project?.name||'No project selected';
  $('#sessionBadge').textContent=st?.project?`PROJECT VERSION ${st.project.version}`:'DETERMINISTIC ENGINE READY';
  $$('.nav').forEach(x=>x.disabled=!st?.loaded&&!['workspace','ai'].includes(x.dataset.page));
  $('#uploadButton').disabled=!!st?.loaded;
  $('.stats').classList.toggle('hidden',!st?.loaded);
  renderExercise();
  if(!st?.loaded){
    for(const id of ['sBranches','sRecords','sIssues','sHash'])$('#'+id).textContent='—';
    $('#properties').className='empty';$('#properties').textContent='Import a CTL and DAT to inspect file properties.';
    for(const id of ['schemaContent','diagnosticCounts','recordList','pageIndex','recordIssues','fieldSummary','repairList','repairIndex','sheetMapping','reconciliation','sheetPreview','gridHead','gridBody','gridIndex','editorValidation','byteMeta','byteCurrent','byteOriginal','byteFields','exportState','history','aiResult'])$('#'+id).replaceChildren();
    grid=null;if(!['workspace','ai'].includes(active))switchPage('workspace');return;
  }
  current=st.record_num;const d=st.diagnosis;
  const layout=$('#layoutBranch').value;$('#layoutBranch').innerHTML='<option value="">Matched destinations</option>'+st.schema.branches.map((b,i)=>`<option value="${i}" ${String(i)===layout?'selected':''}>Assume branch ${i+1} · ${esc(b.table)}</option>`).join('');
  $('#sBranches').textContent=d.branch_count;$('#sRecords').textContent=d.record_count;$('#sIssues').textContent=d.records_with_findings;$('#sHash').textContent=d.sha256.slice(0,14)+'…';
  $('#properties').className='';$('#properties').innerHTML=`<p><strong>${esc(st.project.name)}</strong></p><div class="report-line">${d.byte_length.toLocaleString()} bytes · ${d.record_count.toLocaleString()} records</div><div class="report-line">Encoding: ${esc(d.encoding)} · ${esc(d.record_format)}</div><div class="report-line">Line endings: ${esc(d.line_endings.join(', '))}</div><div class="report-line">Original SHA-256 <code>${esc(st.original_checksum)}</code></div><p>${st.modified?'Working bytes contain approved edits.':'Working bytes match the uploaded source.'}</p>`;
  $('#uploadButton').disabled=!!st.loaded;
  renderSchema();renderDiagnostics();renderRepairs();renderSheet();renderBytes();renderExport();renderAI();
  $('#undo').disabled=!st.undo_count;$('#redo').disabled=!st.redo_count;
  if(!$('#weights').children.length)$('#weights').innerHTML=Object.entries(st.weights).map(([k,v])=>`<label>${esc(k)}<input type="number" data-weight="${esc(k)}" min="0" max="1" step="0.05" value="${v}"></label>`).join('');
}
function renderSchema(){
  $('#schemaContent').className='';$('#schemaContent').innerHTML=`<div class="alert ${st.schema.compatibility==='fully_supported'?'success':''}">${esc(st.schema.compatibility.replaceAll('_',' '))} · ${esc(st.schema.mode)} · ${esc(st.schema.record_format)} ${st.schema.record_length||''}</div>${st.schema.diagnostics.map(x=>`<div class="issue">${esc(x)}</div>`).join('')}`+st.schema.branches.map((b,i)=>{
    const segments=[...b.fields.filter(f=>f.start).map(f=>({...f,label:f.name})),...b.unmapped_intervals.map(g=>({...g,label:'Unmapped',gap:true}))].sort((a,b)=>a.start-b.start);
    return `<div class="panel"><div class="flex"><h3>${i+1}. ${esc(b.table)}</h3><span class="pill neutral">CTL line ${b.source.line}</span></div><p class="mono">${b.when?'WHEN '+esc(b.when):'Unconditional destination'} · ${esc(b.mode)}</p>${b.unsupported.map(x=>`<div class="issue">${esc(x)}</div>`).join('')}<div class="byte-map" aria-label="Byte position map">${segments.map(x=>`<button class="${x.gap?'gap':''}" data-schema-field="${esc(x.label)}" title="Bytes ${x.start}–${x.end}">${esc(x.label)}<br>${x.start}–${x.end}</button>`).join('')}</div><div class="scroller"><table><thead><tr><th>Field / declaration</th><th>Byte range</th><th>Width</th><th>Datatype</th><th>Null / default / transformation</th></tr></thead><tbody>${b.fields.map(f=>`<tr><td><strong>${esc(f.name)}</strong> ${esc(f.filler)}<div class="field-rule" title="${esc(f.source.declaration)}">CTL line ${f.source.line}</div></td><td>${f.start?f.start+'–'+f.end:'Generated'}</td><td>${f.width}</td><td>${esc(f.dtype)} ${esc(f.date_mask)}</td><td class="field-rule">${f.constant!==null?'CONSTANT '+esc(f.constant):''}${f.nullif?'NULLIF '+esc(f.nullif):''}${f.defaultif?'<br>DEFAULTIF '+esc(f.defaultif):''}${f.sql_expression?'<br>SQL: '+esc(f.sql_expression):''}</td></tr>`).join('')}</tbody></table></div></div>`;
  }).join('');
}
function renderDiagnostics(){
  const d=st.diagnosis,r=st.record,p=st.pagination;
  $('#diagnosticCounts').innerHTML='<p>'+Object.entries(d.counts).filter(([,n])=>n).map(([k,n])=>`<span class="pill ${k==='valid'?'good':'bad'}">${n} ${esc(k.replaceAll('_',' '))}</span>`).join(' ')+'</p>';
  $('#recordList').innerHTML=d.records.map(x=>`<tr class="clickable ${x.record===current?'selected':''}" data-select-record="${x.record}"><td>${x.record}</td><td class="mono">${esc(x.record_type)}</td><td>${x.length} / ${x.minimum_length||'?'}</td><td>${x.branches.map(b=>esc(b.table)).join(', ')||'Unmatched'}${x.uncertain_branches.length?'<br>Unevaluable: '+x.uncertain_branches.join(', '):''}</td><td><span class="pill ${x.status==='valid'?'good':'bad'}">${esc(x.classification.replaceAll('_',' '))}</span></td></tr>`).join('');
  $('#pageIndex').textContent=`Page ${p.page} / ${p.pages} · ${p.total} matching records`;$('#prevPage').disabled=p.page<=1;$('#nextPage').disabled=p.page>=p.pages;
  $('#recordTitle').textContent=`Selected record ${current} · ${r?.length||0} bytes · ${r?.line_ending||'none'}`;
  $('#recordIssues').innerHTML=r?.issues.length?r.issues.map(x=>`<div class="issue">${esc(x)}</div>`).join(''):'<div class="alert success">No supported structural discrepancies detected.</div>';
  $('#fieldSummary').innerHTML=r?`<div class="scroller"><table><thead><tr><th>Field</th><th>Position</th><th>Raw (spaces visible)</th><th>Interpreted value</th><th>State</th></tr></thead><tbody>${r.fields.map(f=>`<tr><td>${esc(f.name)}<div class="field-rule">${esc(f.table)}</div></td><td>${f.start}–${f.end}</td><td class="mono">${esc(dot(f.raw))}</td><td>${esc(f.value??'NULL')}</td><td>${f.modified?'Modified':'Source'} ${f.missing_bytes?`· ${f.missing_bytes} missing bytes`:''}</td></tr>`).join('')}</tbody></table></div>`:'';
}
async function loadProposals(){requireProject();const result=await request(api('/repair-proposals?'+new URLSearchParams({offset:repairOffset,limit:50,pending_only:$('#pendingOnly').checked})));repairTotal=result.total;if(repairOffset>=repairTotal&&repairOffset>0){repairOffset=Math.floor((Math.max(1,repairTotal)-1)/50)*50;return loadProposals()}st.proposals=result.proposals;renderRepairs()}
function renderRepairs(){
  $('#repairIndex').textContent=`${repairTotal} proposals · page ${Math.floor(repairOffset/50)+1} / ${Math.max(1,Math.ceil(repairTotal/50))}`;$('#repairPrev').disabled=repairOffset<=0;$('#repairNext').disabled=repairOffset+50>=repairTotal;
  const proposals=st.proposals.filter(p=>!$('#pendingOnly').checked||['PENDING_REVIEW','PROPOSED'].includes(p.status));
  $('#repairList').className='';$('#repairList').innerHTML=proposals.length?proposals.map(p=>{
    const pending=['PENDING_REVIEW','PROPOSED'].includes(p.status);
    return `<article class="panel proposal ${['BEDROCK','GROQ'].includes(p.source)?'ai':''}"><div class="flex"><h3>${pending?`<input type="checkbox" data-proposal="${esc(p.id)}" aria-label="Select proposal for record ${p.record}"> `:''}Record ${p.record} · ${esc(p.kind.replaceAll('_',' '))}${p.rank?' · rank '+p.rank:''}</h3><span class="pill ${['BEDROCK','GROQ'].includes(p.source)?'badge-ai':'neutral'}">${esc(p.source)} · ${esc(p.status)}</span></div><p>${esc(p.reason)}</p><div class="score-bars"><span>Score ${p.candidate_score} / 100</span>${Object.entries(p.score_breakdown||{}).map(([k,v])=>`<span>${esc(k)}: ${v.points.toFixed(1)} / ${(v.weight*100).toFixed(0)}</span>`).join('')}</div>${p.ambiguous?`<div class="issue">Ambiguous: ${p.candidate_count} valid candidates; top-score gap ${Number(p.score_separation).toFixed(2)}. Compare physical changes before approval.</div>`:''}${p.field?`<p><strong>${esc(p.field)}</strong> · bytes ${p.start}–${p.end}</p><div class="two"><div><label>Original value<pre>${esc(dot(p.original_value))}</pre></label></div><label>Proposed value${pending?`<input data-override="${esc(p.id)}" value="${esc(p.proposed_value)}" aria-label="Proposed value for ${esc(p.field)}">`:`<pre>${esc(dot(p.approved_value??p.proposed_value))}</pre>`}</label></div>`:''}<details><summary>Physical changes, evidence & destinations</summary>${p.changes?`<div class="scroller"><table><thead><tr><th>Field</th><th>Bytes</th><th>Before</th><th>After</th></tr></thead><tbody>${p.changes.map(c=>`<tr><td>${esc(c.field)}</td><td>${c.start}–${c.end}</td><td class="mono">${esc(dot(c.original))}</td><td class="mono">${esc(dot(c.proposed))}</td></tr>`).join('')}</tbody></table></div>`:''}<p>Evidence: ${p.evidence?.length?esc(p.evidence.map(e=>`${e.source_reference||e.source_type}: ${e.value??e.matched_value}`).join('; ')):'Structural constraints only; missing business values cannot be inferred.'}</p><p>Destinations after serialization: ${esc((p.destinations||[]).map(d=>d.table).join(', '))}</p>${p.expected_serialized_value?`<pre>${esc(dot(p.expected_serialized_value))}</pre>`:''}<p class="mono">Source record checksum: ${esc(p.record_checksum)}</p></details>${pending?`<div class="btnrow"><button class="primary" data-review="approve" data-id="${esc(p.id)}">Approve & apply</button><button class="secondary" data-review="reject" data-id="${esc(p.id)}">Reject</button><button class="secondary" data-goto-byte="${p.record}">Inspect bytes</button></div>`:''}</article>`;
  }).join(''):'<div class="empty">No pending candidates. Unsupported or evidence-free damage may require manual investigation.</div>';
}
function renderSheet(){
  const sh=st.spreadsheet;if(!sh.sheets?.length){$('#sheetMapping').innerHTML='<div class="notice">No supplementary sheet loaded.</div>';$('#sheetPreview').innerHTML='';$('#reconciliation').innerHTML='';return}
  const previous=$('#sheetSelect')?.value;const selected=sh.sheets.find(s=>s.name===previous)||sh.sheets[0];
  const role=$('#sheetRole')?.value||sh.role;
  const fields=role==='field_specifications'?['FIELD_NAME','START','END','DATATYPE','TARGET_TABLE']:[...new Set(st.schema.branches.flatMap(b=>b.fields.filter(f=>f.start).map(f=>f.name)))].sort();
  const specDefaults={FIELD_NAME:'FIELD_NAME',START_POS:'START',START:'START',END_POS:'END',END:'END',DATA_TYPE:'DATATYPE',DATATYPE:'DATATYPE',TARGET_TABLE:'TARGET_TABLE'};
  const saved=st.reconciliation.sheet===selected.name?st.reconciliation.mapping:role==='field_specifications'?specDefaults:selected.suggested_mapping;
  const keys=st.reconciliation.sheet===selected.name?st.reconciliation.keys:[];
  $('#sheetMapping').innerHTML=`<div class="toolbar"><label>Worksheet<select id="sheetSelect">${sh.sheets.map(s=>`<option ${s.name===selected.name?'selected':''} value="${esc(s.name)}">${esc(s.name)} · ${s.row_count} rows · ${esc(s.kind)}</option>`).join('')}</select></label><label>Role<select id="sheetRole">${['reference_values','transaction_values','example_records','field_specifications'].map(r=>`<option ${r===role?'selected':''} value="${r}">${r.replaceAll('_',' ')}</option>`).join('')}</select></label></div><div class="mapping-row"><strong>Spreadsheet header</strong><strong>CTL field</strong><strong>Join key</strong></div>${selected.headers.map(h=>`<div class="mapping-row"><span>${esc(h)}</span><select data-map="${esc(h)}" aria-label="Map ${esc(h)}"><option value="">Ignore</option>${fields.map(f=>`<option value="${esc(f)}" ${saved?.[h]===f?'selected':''}>${esc(f)}</option>`).join('')}</select><label><input type="checkbox" data-key="${esc(h)}" ${role==='field_specifications'?'disabled':''} ${keys.includes(saved?.[h])?'checked':''}> Key</label></div>`).join('')}<div class="btnrow"><button class="primary" id="reconcile">Reconcile mapped values</button><span class="hint">Exact key matches only. Leading zeros remain significant.</span></div>`;
  $('#sheetSelect').onchange=renderSheet;$('#sheetRole').onchange=renderSheet;$('#reconcile').onclick=()=>reconcileSheet().catch(fail);
  $('#sheetPreview').innerHTML=`<p>${esc(sh.precision_policy)} · Header row ${selected.header_row}. Preview ${selected.rows.length} / ${selected.row_count} rows.</p>${(sh.warnings||[]).slice(0,5).map(w=>`<div class="issue">${esc(w)}</div>`).join('')}<div class="scroller"><table><thead><tr>${selected.headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${selected.rows.map(r=>`<tr>${selected.headers.map(h=>`<td title="${esc(r.references[h])}">${esc(r.values[h])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  const rec=st.reconciliation;
  $('#reconciliation').innerHTML=rec.sheet?`<div class="alert ${rec.conflicts.length?'':'success'}">${rec.matched_records} matched records · ${rec.conflicts.length} conflicts in displayed result · ${rec.duplicate_keys.length} duplicate keys · ${rec.missing_supplement_records.length} records without a unique supplement match. Conflicts require approval in the repair queue.</div>${rec.spec_conflicts?.length?rec.spec_conflicts.map(x=>`<div class="issue">${esc(x.field)} · ${esc(x.source_reference)}: ${esc(x.issues.join('; '))}</div>`).join(''):''}${rec.duplicate_keys.length?`<pre>${esc(JSON.stringify(rec.duplicate_keys,null,2))}</pre>`:''}`:'';
}
async function reconcileSheet(){
  const mapping={},keys=[];$$('[data-map]').forEach(e=>{if(e.value){mapping[e.dataset.map]=e.value;if($$('[data-key]').find(k=>k.dataset.key===e.dataset.map)?.checked)keys.push(e.value)}});
  await mutate('/map-spreadsheet',{sheet:$('#sheetSelect').value,role:$('#sheetRole').value,mapping,keys});show('Reconciliation complete. Existing DAT values are preserved pending review.');
}
function renderAI(){const run=st.ai_runs.at(-1);$('#aiResult').innerHTML=run?`<div class="panel"><span class="pill badge-ai">${esc(run.provider||aiStatus?.provider||'AI')} · ${esc(run.capability)} · ${esc(run.result.status)}</span><p>${esc(run.result.summary)}</p>${run.result.observations.map(x=>`<div class="notice">${esc(x)}</div>`).join('')}${run.result.mappings.map(m=>`<div class="issue">${esc(m.sheet)} / ${esc(m.header)} → ${esc(m.field_name)}: ${esc(m.reason)} <button class="secondary" data-ai-map="${esc(m.header)}" data-ai-field="${esc(m.field_name)}" data-ai-sheet="${esc(m.sheet)}">Use mapping</button></div>`).join('')}<p class="small">Tokens: ${esc(run.usage.inputTokens??'?')} input / ${esc(run.usage.outputTokens??'?')} output · ${run.latency_ms} ms · ${run.estimated_cost_usd===null?'Cost unpriced':`Estimated $${run.estimated_cost_usd.toFixed(6)}`}</p></div>`:''}
async function runAI(){requireProject();const capability=$('#aiCapability').value;const share_values=$('#shareValues').checked;if(share_values&&!await confirmAction(`Share fields and matching evidence for record ${current} with ${aiStatus?.provider||'your AI provider'} (${aiStatus?.model_id||'configured model'})?`))return;$('#runAI').disabled=true;try{const result=await request(api('/ai/recover'),{method:'POST',body:JSON.stringify({version:st.project.version,capability,record:current,share_values})});st=result.state;render();show(result.skipped?result.reason:'AI review saved. Any data modifications still require your approval.')}finally{$('#runAI').disabled=aiStatus?!aiStatus.ai_available:false}}
async function review(ids,action){
  if(!ids.length)throw Error('Select one or more proposals first');
  const overrides={};ids.forEach(id=>{const input=$$('[data-override]').find(e=>e.dataset.override===id);if(input)overrides[id]=input.value});
  if(action==='approve'&&!await confirmAction(`Approve and apply ${ids.length} selected proposal(s)? Review the evidence and physical changes first.`))return;
  await mutate('/repairs/review',{ids,action,overrides});show(`Proposals ${action==='approve'?'approved and applied':'rejected'}.`)
}
async function loadGrid(){requireProject();const query=new URLSearchParams({page:gridPage,page_size:25,record_type:$('#gridType').value});if($('#layoutBranch').value!=='')query.set('layout_branch',$('#layoutBranch').value);grid=await request(api('/records?'+query));renderGrid()}
function renderGrid(){
  if(!grid)return;const filter=$('#columnSearch').value.toUpperCase();const columns=new Map();
  grid.records.forEach(r=>r.fields.forEach(f=>{if(!filter||f.name.includes(filter))columns.set(`${f.branch}:${f.name}`,f)}));
  const cols=[...columns.entries()];
  $('#gridHead').innerHTML='<tr><th class="frozen">Select / #</th>'+cols.map(([key,f])=>`<th title="${esc(f.table)} · bytes ${f.start}–${f.end}">${esc(f.name)}<div class="field-rule">branch ${f.branch+1} · ${f.start}–${f.end}</div></th>`).join('')+'</tr>';
  $('#gridBody').innerHTML=grid.records.map(r=>`<tr><td class="frozen"><input type="checkbox" data-row-select="${r.record}" ${selectedRows.has(r.record)?'checked':''} aria-label="Select record ${r.record}"><button class="secondary compact" data-editor-record="${r.record}">${r.record}</button></td>${cols.map(([key,f])=>{const value=r.fields.find(x=>`${x.branch}:${x.name}`===key);if(!value)return '<td class="readonly">—</td>';const suggested=value.pending_proposal||st.proposals.some(p=>p.record===r.record&&p.field===f.name&&['PROPOSED','PENDING_REVIEW'].includes(p.status));const invalid=r.findings.some(x=>x.field===f.name);return `<td class="${value.modified?'modified ':''}${suggested?'suggested ':''}${invalid?'invalid':''}">${value.readonly?esc(value.value):`<input aria-label="Record ${r.record} ${esc(f.name)} branch ${f.branch+1}" data-cell data-record="${r.record}" data-field="${esc(f.name)}" data-branch="${f.branch}" data-original="${esc(value.raw)}" value="${esc(value.raw)}" title="Source: ${esc(dot(value.original))} · ${f.width} bytes">`}</td>`}).join('')}</tr>`).join('');
  $('#gridIndex').textContent=`Page ${gridPage} / ${Math.max(1,Math.ceil(grid.total/25))} · ${selectedRows.size} selected`;
  $('#gridPrev').disabled=gridPage<=1;$('#gridNext').disabled=gridPage*25>=grid.total;
  $('#editorValidation').innerHTML=($('#layoutBranch').value!==''?'<div class="issue">An assumed CTL layout is displayed. This does not change routing until you explicitly edit the record marker or condition fields.</div>':'')+grid.records.filter(r=>r.issues.length).map(r=>`<div class="small"><strong>Record ${r.record}:</strong> ${esc(r.issues.join('; '))}</div>`).join('')||'All displayed records pass supported structural checks.';
}
async function saveCell(input){if(input.value===input.dataset.original)return;if(cellBusy)throw Error('Wait for the current edit to finish');cellBusy=true;try{await mutate('/records',{edits:[{record:Number(input.dataset.record),field:input.dataset.field,value:input.value,align:$('#editAlign').value,branch:Number(input.dataset.branch)}]},'PATCH');await loadGrid();show('Manual field edit saved; unrelated bytes preserved.')}finally{cellBusy=false}}
function renderBytes(){
  const r=st.record;if(!r)return;$('#byteRecord').value=current;
  $('#byteMeta').innerHTML=`<p>Record ${current} · ${r.length} bytes · ${esc(r.line_ending)} · <code>${esc(r.checksum)}</code>${r.preview_truncated?' · preview limited to 16,384 bytes':''}</p>`;
  const hex=$('#hexView').checked, raw=r.raw, original=r.original_raw;
  function physical(value,other,highlight){
    let out='';const stride=hex?16:64;
    for(let i=0;i<value.length;i+=stride){let chars='';for(let j=i;j<Math.min(i+stride,value.length);j++){
      const c=value[j], f=r.fields.find(f=>f.start&&j>=f.start-1&&j<f.end), changed=highlight&&(j>=other.length||other[j]!==c);
      chars+=`<span class="byte-char ${changed?'changed':''}" ${f?`data-field="${esc(f.name)}"`:''} title="Byte ${j+1}${f?' · '+esc(f.name):' · unmapped'}">${hex?c.charCodeAt(0).toString(16).padStart(2,'0')+' ':esc(c===' '?'·':c==='\t'?'→':c)}</span>`;
    }out+=`<div class="byte-row"><span>${String(i+1).padStart(6,'0')}</span><span>${chars}</span></div>`}return out||'<span>Empty record</span>';
  }
  // Use byte hex from the backend for cp1252 rather than Unicode code points.
  if(hex){const toText=h=>Array.from(h.matchAll(/../g),m=>String.fromCharCode(parseInt(m[0],16))).join('');$('#byteCurrent').innerHTML=physical(toText(r.hex),toText(r.original_hex),true);$('#byteOriginal').innerHTML=physical(toText(r.original_hex),toText(r.hex),false)}
  else{$('#byteCurrent').innerHTML=physical(raw,original,true);$('#byteOriginal').innerHTML=physical(original,raw,false)}
  $('#byteFields').innerHTML='<p>'+r.fields.filter(f=>f.start).map(f=>`<span class="pill ${f.modified?'bad':'neutral'}">${esc(f.name)} ${f.start}–${f.end}</span>`).join(' ')+'</p>';
}
function renderExport(){const d=st.diagnosis;$('#exportState').innerHTML=`<div class="alert ${['structurally_validated','validated_with_warnings'].includes(d.export_status)?'success':''}">${esc(d.export_status.replaceAll('_',' '))} · ${d.records_with_findings} record(s) with findings</div><p><strong>Current SHA-256:</strong> <code>${esc(d.sha256)}</code></p><p>${esc(d.validation_scope)}</p><p>${st.generated_checksum===d.sha256?'Current version has been generated and re-parsed.':'Generate the current version before downloading.'}</p>`;$('#downloadDat').disabled=st.generated_checksum!==d.sha256;$('#draftOption').classList.toggle('hidden',!draftAllowed);$('#history').innerHTML=st.history.slice().reverse().map(h=>`<div class="report-line small"><strong>${esc(h.action)}</strong> · ${esc(h.timestamp)}${h.record?' · record '+h.record:''}${h.records?' · records '+esc((Array.isArray(h.records)?h.records.join(', '):String(h.records))):''}${h.after_checksum?'<div class="mono">'+esc(h.after_checksum)+'</div>':''}</div>`).join('')||'<p>No changes applied.</p>'}
async function loadAIStatus(){
  const s=await request('/api/ai/status');aiStatus=s;renderAIStatus();return s;
}
function renderAIStatus(){
  const s=aiStatus;if(!s)return;
  const state=String(s.connection_status||'not_configured').replaceAll('_',' ');
  const provider=s.provider==='AmazonBedrockProvider'?'Amazon Bedrock':s.provider||'AI';
  $('#aiStatus').innerHTML=`<div class="alert ${s.connection_status==='connected'?'success':''}">${esc(state)} · AI ${s.ai_available?'available':'unavailable'}</div><div class="report-line">Provider: <strong>${esc(provider)}</strong></div><div class="report-line">Model: <strong>${esc(s.model_id||'Not configured')}</strong></div>${s.region?`<div class="report-line">AWS Region: ${esc(s.region)}</div>`:''}<div class="report-line">Model access: ${s.model_accessible===true?'Verified by a live test':s.model_accessible===false?'Test failed':'Not tested'}</div><div class="report-line">Last successful test: ${esc(s.last_successful_test||'None')}</div>${s.error?`<div class="issue">${esc(s.error)}</div>`:''}`;
  $('#aiReviewStatus').textContent=`${provider} · ${s.model_id||'No model configured'} · ${state}. ${s.ai_available?'AI output is reviewed before any correction.':'Check AI connection settings; you can still inspect, edit and export supported files.'}`;
  $('#runAI').disabled=!s.ai_available;
}
function selectedSample(){return samples.find(s=>s.id===$('#sampleSelect').value)}
function sampleVariant(sample){return sample?.variants.find(v=>v.id===$('#sampleVariant').value)}
function expectedLabel(sample,variant){return !sample.supported?'Compatibility example · validated export blocked':variant.expected_valid?'Expected: no findings':`Expected: ${variant.expected_records_with_findings??'one or more'} record(s) with findings`}
async function loadSamples(){
  const manifest=await request('/api/samples');samples=manifest.samples||[];
  $('#sampleCount').textContent=`${samples.length} layouts · up to 4,800 bytes`;
  $('#sampleSelect').innerHTML=[true,false].map(supported=>`<optgroup label="${supported?'Supported practice layouts':'Unsupported declarations · inspect limitations'}">${samples.filter(s=>s.supported===supported).map(s=>`<option value="${esc(s.id)}">${esc(s.title)}</option>`).join('')}</optgroup>`).join('');
  if(samples.some(s=>s.id==='receipt_4800'))$('#sampleSelect').value='receipt_4800';
  $('#sampleSelect').disabled=!samples.length;$('#loadSample').disabled=!samples.length;$('#downloadSample').disabled=!samples.length;
  updateSampleVariants();renderExercise();
}
function updateSampleVariants(){
  const s=selectedSample();$('#sampleVariant').innerHTML=(s?.variants||[]).map(v=>`<option value="${esc(v.id)}">${esc(v.label)}</option>`).join('');
  if(s?.variants.some(v=>v.id==='missing_spaces'))$('#sampleVariant').value='missing_spaces';
  $('#sampleVariant').disabled=!s;renderSampleDescription();
}
function renderSampleDescription(){
  const s=selectedSample(),v=sampleVariant(s);if(!s||!v)return;
  $('#sampleDescription').innerHTML=`<div class="flex"><span class="pill ${s.supported?'good':'bad'}">${s.supported?'Supported in Studio':'Oracle syntax outside Studio support'}</span><span class="small">${s.record_count} records · ${s.record_length.toLocaleString()} bytes per correct record · ${esc(s.record_format)} · ${esc(s.line_ending)}</span></div><p>${esc(s.description)}</p><div class="notice"><strong>${esc(expectedLabel(s,v))}</strong><p>${esc(v.exercise)}</p>${s.keys.length?`<span>Reconciliation key: <code>${esc(s.keys.join(' + '))}</code></span>`:''}</div>`;
}
function renderExercise(){
  const e=$('#activeExercise'),loaded=st?.sample;e.classList.toggle('hidden',!loaded);if(!loaded){e.replaceChildren();return}
  const sample=samples.find(s=>s.id===loaded.id)||loaded;
  const variant=sample.variants?.find(v=>v.id===loaded.variant);
  e.innerHTML=`<div class="flex"><div><div class="eyebrow">Loaded practice case</div><h2>${esc(loaded.title||sample.title)}</h2></div><span class="pill ${loaded.supported?'good':'bad'}">${loaded.supported?'Supported layout':'Compatibility limit'}</span></div><p><strong>${esc(variant?.label||String(loaded.variant||''))}</strong> · ${esc(loaded.exercise||variant?.exercise||loaded.description||'')}</p>${loaded.keys?.length?`<p>For reference evidence, use <code>${esc(loaded.keys.join(' + '))}</code> as the reconciliation key. Review proposed values before approval.</p>`:''}<div class="btnrow"><button class="secondary compact" data-page-link="diagnostics">Inspect findings</button><button class="secondary compact" data-page-link="repairs">Review corrections</button><button class="secondary compact" data-page-link="export">Generate output</button></div>`;
}
function resetProjectView(){
  current=1;pageNumber=1;gridPage=1;grid=null;repairOffset=0;repairTotal=0;selectedRows.clear();
  for(const id of ['weights','gridHead','gridBody'])$('#'+id).replaceChildren();
  for(const id of ['recordSearch','typeFilter','statusFilter','gridType','columnSearch','layoutBranch'])$('#'+id).value='';
  $('#sortFilter').value='record';$('#draftExport').checked=false;$('#shareValues').checked=false;
}
async function loadSample(){
  const sample=selectedSample(),variant=sampleVariant(sample);if(!sample||!variant)throw Error('Choose a practice case first');
  const button=$('#loadSample');button.disabled=true;button.textContent='Creating practice project…';$('#sampleSelect').disabled=true;$('#sampleVariant').disabled=true;
  try{
    const created=await request('/api/projects',{method:'POST',body:JSON.stringify({name:`${sample.title} · ${variant.label}`.slice(0,120)})});
    pid=created.project.id;st=created;resetProjectView();render();
    st=await request(api('/sample'),{method:'POST',body:JSON.stringify({version:st.project.version,sample_id:sample.id,variant:variant.id})});
    render();await listProjects();switchPage('diagnostics');show(`Practice case loaded. ${expectedLabel(sample,variant)}. Follow the exercise above.`);
  }finally{button.disabled=false;button.textContent='Load practice case →';$('#sampleSelect').disabled=false;$('#sampleVariant').disabled=false;await listProjects().catch(()=>{})}
}
async function listProjects(){const projects=await request('/api/projects');$('#projectList').innerHTML=projects.length?projects.map(p=>`<div class="project-item"><button class="secondary" data-open-project="${esc(p.id)}">${esc(p.name)}<div class="small">Version ${p.version}</div></button><button class="danger compact" data-delete-project="${esc(p.id)}" aria-label="Delete ${esc(p.name)}">Delete</button></div>`).join(''):'<p>Create your first project to begin.</p>'}
async function openProject(id){pid=id;resetProjectView();await refresh();await listProjects();if(!st.loaded)show('Project created. Import its CTL and DAT files or choose a practice case.');if(active==='editor'&&st.loaded)await loadGrid();if(active==='repairs'&&st.loaded)await loadProposals()}
function download(path){requireProject();window.location.assign(api(path))}
async function initialize(){const s=await request('/api/session');draftAllowed=s.draft_export_enabled;if(!s.authenticated){if(s.login_required){$('#login').classList.remove('hidden');$('#application').classList.add('hidden');return}await request('/api/session',{method:'POST',body:'{}'})}$('#login').classList.add('hidden');$('#application').classList.remove('hidden');render();const results=await Promise.allSettled([listProjects(),loadSamples(),loadAIStatus()]);results.filter(r=>r.status==='rejected').forEach(r=>fail(r.reason))}

$$('.nav').forEach(e=>e.onclick=()=>switchPage(e.dataset.page));
$('#sampleSelect').onchange=updateSampleVariants;$('#sampleVariant').onchange=renderSampleDescription;
$('#sampleForm').onsubmit=e=>{e.preventDefault();loadSample().catch(fail)};
$('#downloadSample').onclick=()=>{const s=selectedSample();if(s)window.location.assign(`/api/samples/${encodeURIComponent(s.id)}/download`)};
$('#loginForm').onsubmit=async e=>{e.preventDefault();try{await request('/api/session',{method:'POST',body:JSON.stringify({access_token:$('#accessToken').value})});$('#accessToken').value='';await initialize()}catch(e){fail(e)}};
$('#createForm').onsubmit=async e=>{e.preventDefault();try{const p=await request('/api/projects',{method:'POST',body:JSON.stringify({name:$('#newProject').value})});await openProject(p.project.id)}catch(e){fail(e)}};
$('#recordFormat').onchange=()=>{$('#recordLength').disabled=$('#recordFormat').value!=='fixed';$('#recordLength').required=!$('#recordLength').disabled};
$('#uploadForm').onsubmit=async e=>{e.preventDefault();try{
  if(!pid){const p=await request('/api/projects',{method:'POST',body:JSON.stringify({name:$('#newProject').value||'DAT review'})});pid=p.project.id;st=p}
  if(st.loaded)throw Error('Create a new project to import a different source set');
  const fd=new FormData(e.target);fd.set('version',st.project.version);if(!fd.get('record_length'))fd.delete('record_length');
  const progress=$('#uploadProgress');progress.classList.remove('hidden');$('#uploadButton').disabled=true;
  st=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST',api('/files'));xhr.setRequestHeader('X-Studio-Request','1');xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.value=e.loaded/e.total*100};xhr.onload=()=>{try{const r=JSON.parse(xhr.responseText);xhr.status<300?resolve(r):reject(Error(typeof r.detail==='string'?r.detail:JSON.stringify(r.detail)))}catch(e){reject(e)}};xhr.onerror=()=>reject(Error('Upload connection failed'));xhr.send(fd)});
  current=1;pageNumber=1;render();await listProjects();switchPage('diagnostics');show('Files imported. Diagnostics and repair hypotheses are ready for review.');
}catch(e){fail(e)}finally{$('#uploadProgress').classList.add('hidden');$('#uploadButton').disabled=!!st?.loaded}};
$('#applyFilters').onclick=()=>{pageNumber=1;refresh().catch(fail)};$('#recordSearch').onkeydown=e=>{if(e.key==='Enter')$('#applyFilters').click()};
$('#prevPage').onclick=()=>{pageNumber--;refresh().catch(fail)};$('#nextPage').onclick=()=>{pageNumber++;refresh().catch(fail)};
$('#openEditor').onclick=()=>switchPage('editor');
$('#analyze').onclick=async()=>{try{const weights={};$$('[data-weight]').forEach(e=>weights[e.dataset.weight]=Number(e.value));await mutate('/analyze',{weights});show('Bounded reconstruction finished. Review candidates and their score separation.')}catch(e){fail(e)}};
$('#pendingOnly').onchange=()=>{repairOffset=0;loadProposals().catch(fail)};$('#repairPrev').onclick=()=>{repairOffset-=50;loadProposals().catch(fail)};$('#repairNext').onclick=()=>{repairOffset+=50;loadProposals().catch(fail)};$('#approveSelected').onclick=()=>review($$('[data-proposal]:checked').map(e=>e.dataset.proposal),'approve').catch(fail);$('#rejectSelected').onclick=()=>review($$('[data-proposal]:checked').map(e=>e.dataset.proposal),'reject').catch(fail);
$('#runAI').onclick=()=>runAI().catch(fail);
$('#columnSearch').oninput=renderGrid;$('#loadGrid').onclick=()=>{gridPage=1;loadGrid().catch(fail)};$('#gridPrev').onclick=()=>{gridPage--;loadGrid().catch(fail)};$('#gridNext').onclick=()=>{gridPage++;loadGrid().catch(fail)};
$('#bulkEdit').onclick=async()=>{try{if(!selectedRows.size)throw Error('Select rows first');if(!await confirmAction(`Apply the entered value to ${selectedRows.size} selected records?`))return;await mutate('/records',{edits:[...selectedRows].map(record=>({record,field:$('#bulkField').value.toUpperCase(),value:$('#bulkValue').value,align:$('#editAlign').value,...($('#layoutBranch').value!==''?{branch:Number($('#layoutBranch').value)}:{})}))},'PATCH');await loadGrid();show('Bulk edits applied atomically.')}catch(e){fail(e)}};
for(const action of ['undo','redo','reset'])$('#'+action).onclick=async()=>{try{if(action==='reset'&&!await confirmAction('Restore all original uploaded DAT bytes? This action is undoable.'))return;await mutate('/history/'+action,{});if(active==='editor')await loadGrid();show(action==='reset'?'Original DAT restored.':`${action} completed.`)}catch(e){fail(e)}};
$('#hexView').onchange=()=>st&&renderBytes();$('#inspectByte').onclick=()=>refresh(Number($('#byteRecord').value)).catch(fail);
$('#revalidate').onclick=async()=>{try{requireProject();const r=await request(api('/validate'),{method:'POST'});show(`${r.export_status.replaceAll('_',' ')} · byte round-trip ${r.round_trip_identical?'identical':'failed'}`);await refresh()}catch(e){fail(e)}};
$('#generate').onclick=async()=>{try{requireProject();const r=await request(api('/generate'),{method:'POST',body:JSON.stringify({version:st.project.version,draft:$('#draftExport').checked})});st=r.state;render();show('DAT generated and re-parsed. The current output is ready to download.')}catch(e){fail(e)}};
$('#downloadDat').onclick=()=>{try{download('/download')}catch(e){fail(e)}};$('#downloadOriginal').onclick=()=>{try{download('/download?original=true')}catch(e){fail(e)}};$('#downloadReport').onclick=()=>{try{download('/report')}catch(e){fail(e)}};$('#downloadAudit').onclick=()=>{try{download('/audit')}catch(e){fail(e)}};
$('#testAI').onclick=async()=>{const b=$('#testAI');b.disabled=true;b.textContent='Testing connection…';try{await request('/api/ai/test',{method:'POST'});await loadAIStatus();show('Connection test finished. Review the model access result below.')}catch(e){fail(e);await loadAIStatus().catch(()=>{})}finally{b.disabled=false;b.textContent='Test AI connection'}};
document.addEventListener('click',async e=>{const el=e.target.closest('button,[data-select-record]');if(!el)return;try{
  if(el.dataset.pageLink)switchPage(el.dataset.pageLink);
  if(el.dataset.openProject)await openProject(el.dataset.openProject);
  if(el.dataset.deleteProject&&await confirmAction('Permanently delete this local project, including original files and history?')){await request(`/api/projects/${encodeURIComponent(el.dataset.deleteProject)}`,{method:'DELETE'});if(pid===el.dataset.deleteProject){pid=null;st=null;location.reload()}await listProjects()}
  if(el.dataset.selectRecord)await refresh(Number(el.dataset.selectRecord));
  if(el.dataset.editorRecord){await refresh(Number(el.dataset.editorRecord));switchPage('bytes')}
  if(el.dataset.gotoByte){await refresh(Number(el.dataset.gotoByte));switchPage('bytes')}
  if(el.dataset.review)await review([el.dataset.id],el.dataset.review);
  if(el.dataset.schemaField){$('#columnSearch').value=el.dataset.schemaField;switchPage('editor')}
  if(el.dataset.aiMap){$('#sheetSelect').value=el.dataset.aiSheet;renderSheet();const select=$$('[data-map]').find(x=>x.dataset.map===el.dataset.aiMap);if(select){select.value=el.dataset.aiField;show('Mapping selected. Choose its join keys and reconcile to confirm.')}}
}catch(e){fail(e)}});
document.addEventListener('change',e=>{const input=e.target;if(input.matches('[data-row-select]')){const n=Number(input.dataset.rowSelect);input.checked?selectedRows.add(n):selectedRows.delete(n);$('#gridIndex').textContent=`Page ${gridPage} · ${selectedRows.size} selected`}if(input.matches('[data-cell]'))saveCell(input).catch(fail)});
document.addEventListener('keydown',async e=>{
  if(active==='editor'&&(e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();$('#'+(e.shiftKey?'redo':'undo')).click();return}
  const input=e.target;if(!input.matches('[data-cell]'))return;
  if(e.key==='Escape'){input.value=input.dataset.original;input.blur();return}
  const cells=$$('[data-cell]'),i=cells.indexOf(input);let target=null;
  if(e.key==='ArrowUp'||e.key==='ArrowDown'){
    const rows=$$('#gridBody tr'),rowIndex=rows.indexOf(input.closest('tr')),next=rows[rowIndex+(e.key==='ArrowUp'?-1:1)];
    if(next){const candidates=Array.from(next.querySelectorAll('[data-cell]'));target=candidates.find(x=>x.dataset.field===input.dataset.field&&x.dataset.branch===input.dataset.branch)||candidates.find(x=>x.dataset.field===input.dataset.field)||candidates[0]}
  }else if(e.key==='Enter'||e.key==='Tab'||((e.ctrlKey||e.metaKey)&&['ArrowLeft','ArrowRight'].includes(e.key))){
    const step=e.shiftKey||e.key==='ArrowLeft'?-1:1;target=cells[Math.max(0,Math.min(cells.length-1,i+step))];
  }else return;
  e.preventDefault();const destination=target?{...target.dataset}:null;
  try{await saveCell(input);if(destination){const fresh=$$('[data-cell]').find(x=>x.dataset.record===destination.record&&x.dataset.field===destination.field&&x.dataset.branch===destination.branch);fresh?.focus()}}catch(err){fail(err)}
});
initialize().catch(fail);
