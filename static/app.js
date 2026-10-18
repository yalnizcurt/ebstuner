'use strict';

const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = value => structuredClone(value);
const columns = [
  {key:'field_name',label:'Field Name',className:'frozen'},
  {key:'id',label:'Field ID',className:'category-col'},
  {key:'category_functional_group',label:'Category / Functional Group',className:'category-col'},
  {key:'mapping_type',label:'Mapping Type',className:'mapping-col'},
  {key:'start_pos',label:'Start Pos',className:'position-col'},
  {key:'end_pos',label:'End Pos',className:'position-col'},
  {key:'length',label:'Length',className:'length-col'},
  {key:'datatype_conversion',label:'Data Type / Oracle Conversion Function',className:'conversion-col'},
  {key:'padding_alignment',label:'Padding / Alignment Rule',className:'padding-col'},
  {key:'raw_sample',label:'Raw Sample Value in DAT File (Quoted)',className:'sample-col'},
  {key:'cleansed_output',label:'Cleansed Output in Oracle Staging Table',className:'sample-col'},
  {key:'sequence',label:'Sequence',className:'position-col'},
  {key:'record_type',label:'Record Type',className:'category-col'},
  {key:'mandatory',label:'Mandatory',className:'position-col'},
  {key:'alignment',label:'Alignment',className:'category-col'},
  {key:'padding_byte',label:'Padding Byte',className:'position-col'},
  {key:'padding_direction',label:'Padding Direction',className:'category-col'},
  {key:'format',label:'Format',className:'category-col'},
  {key:'allowed_values',label:'Allowed Values',className:'sample-col'},
  {key:'conditional_rules',label:'Conditional Rules',className:'sample-col'},
  {key:'description',label:'Description',className:'sample-col'},
  {key:'remarks',label:'Remarks',className:'sample-col'},
  {key:'source_reference',label:'Source Reference',className:'category-col'}
];
let state = null;
let projectId = null;
let stage = 'upload';
let draftFields = [];
let draftOptions = {};
let draftLayouts = [];
let specDirty = false;
let valueEdits = new Map();
let busy = false;
let pollTimer = null;
let selectedFieldId = null;
let pendingFocus = null;
let batchState = null;
let batchSpecId = null;
let batchSpecDraft = null;
let batchSpecDirty = false;
let batchSpecEditing = false;
let batchDatId = null;
let batchRecords = [];
let batchRecord = null;
let batchRecordDetail = null;
let batchOffset = 0;
let batchTotal = 0;
let batchFieldId = null;
let batchRuleIndex = null;

function notify(message, error = false) {
  $('#notice').className = `notice${error ? ' error' : ''}`;
  $('#noticeText').textContent = message;
}
function errorMessage(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(x => x.msg || x.message || 'Review the entered values.').join(' ');
  return detail?.message || detail?.error || 'The action could not be completed. Review your files and try again.';
}
async function request(path, options = {}) {
  const response = await fetch(path, {...options, headers:{'X-Studio-Request':'1', ...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...options.headers}});
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) {
      $('#loginPanel').classList.remove('hidden');
      $('#application').classList.add('hidden');
    }
    if (response.status === 409) throw Error(`${errorMessage(body?.detail || body)} Your unsaved changes are preserved.`);
    throw Error(errorMessage(body?.detail || body));
  }
  return body;
}
const projectPath = suffix => `/api/projects/${encodeURIComponent(projectId)}/studio${suffix}`;
function requireProject() { if (!projectId || !state) throw Error('Upload a specification and DAT file first.'); }
function confirmAction(message) {
  return new Promise(resolve => {
    const dialog = $('#confirmDialog');
    $('#confirmMessage').textContent = message;
    dialog.returnValue = 'cancel';
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true});
    dialog.showModal();
  });
}
function applyState(next) {
  state = next.state || next;
  projectId = state.project.id;
  if (!specDirty) {
    draftFields = clone(state.spec?.fields || []);
    draftOptions = clone(state.spec?.options || {});
    draftLayouts = clone(state.spec?.layouts || []);
  }
  render();
  schedulePoll();
}
async function action(path, payload = {}, method = 'POST') {
  requireProject();
  const next = await request(projectPath(path), {method, body:JSON.stringify({version:state.project.version, ...payload})});
  applyState(next);
  await listProjects();
  return state;
}
async function withBusy(button, label, work) {
  if (busy) return;
  busy = true;
  $('#newProjectButton').disabled = true;
  const previous = button?.textContent;
  if (button) { button.disabled = true; button.textContent = label; }
  try { return await work(); }
  catch (error) { notify(error.message, true); }
  finally { busy = false; if (button) button.textContent = previous; render(); }
}
function activeJobs() { return (state?.jobs || []).filter(job => ['queued','running','retry_wait'].includes(job.status)); }
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!projectId || !activeJobs().length) return;
  pollTimer = setTimeout(async () => {
    try {
      const record = state.review?.record?.number || 1;
      const next = await request(projectPath(`?record=${record}`));
      applyState(next);
    } catch (error) {
      notify(`Could not refresh analysis progress. ${error.message}`, true);
      pollTimer = setTimeout(schedulePoll, 5000);
    }
  }, 2200);
}
async function refreshProject(record = state?.review?.record?.number || 1) {
  requireProject();
  const next = await request(projectPath(`?record=${record}`));
  applyState(next);
}
async function listProjects() {
  const projects = await request('/api/projects');
  $('#projectList').innerHTML = projects.length ? projects.map(project => `<button class="project-link ${project.id === projectId ? 'active' : ''}" data-open-project="${esc(project.id)}">${esc(project.name)}<small>Saved · version ${esc(project.version)}</small></button>`).join('') : '<p class="sidebar-note">Your saved reviews will appear here.</p>';
}
async function openProject(id) {
  if ((specDirty || valueEdits.size || batchSpecDirty) && !await confirmAction('Leave your unsaved changes and open this saved project?')) return;
  specDirty = false; valueEdits.clear(); batchSpecDirty = false; batchSpecEditing = false; batchState = null; batchSpecId = null; batchSpecDraft = null; batchDatId = null; batchRecords = []; batchRecordDetail = null;
  const next = await request(`/api/projects/${encodeURIComponent(id)}/studio`);
  applyState(next);
  await loadBatch();
  setStage(batchState?.batch?.specs?.length || batchState?.batch?.dats?.length ? 'batch' : state.spec?.approved ? 'dat' : state.document ? 'spec' : 'upload');
  if(state.stage==='legacy')notify(state.notice||'This saved project uses the Advanced CTL workstation. Open it from the navigation link.');
  await listProjects();
}
function setStage(next) {
  if (next === 'batch') { stage = next; render(); window.scrollTo({top:0,behavior:'smooth'}); return; }
  if (next !== 'upload' && !state?.document) { notify('Upload the specification and original DAT to begin.'); return; }
  if (next === 'spec' && valueEdits.size) { notify('Save your DAT value changes before editing the specification.'); return; }
  if (next === 'dat' && (!state?.spec?.approved || specDirty)) { notify('Approve the reviewed specification before analyzing DAT values.'); return; }
  stage = next;
  render();
  window.scrollTo({top:0, behavior:'smooth'});
}
function issueText(issue) { return typeof issue === 'string' ? issue : issue.message || issue.reason || issue.code || 'Review this item.'; }
function fieldIssues(field) {
  return (state?.spec?.issues || []).filter(issue => typeof issue !== 'string' && (issue.field_id === field.id || issue.field === field.id || issue.field === field.field_name));
}
function reviewField(id) { return state?.review?.record?.fields?.find(field => field.id === id); }
function currentValue(field) {
  const key = `${state.review.record.number}:${field.id}`;
  return valueEdits.has(key) ? valueEdits.get(key).value : field.value ?? '';
}
function render() {
  $('#projectTitle').textContent = state?.project?.name || 'Prepare a DAT file for import';
  $('#pageSubtitle').textContent = stage === 'batch' ? 'Review files, approve rules, and inspect each record before export.' : stage === 'upload' ? 'Review your specification and correct values without counting spaces.' : stage === 'spec' ? 'Your approved specification controls how the file is formatted and checked.' : 'Review actual values beside the approved specification. Padding is handled for you.';
  $('#saveStatus').textContent = specDirty || valueEdits.size ? 'Unsaved changes' : state ? `Saved · version ${state.project.version}` : 'No project selected';
  $('#saveStatus').className = `status-tag ${specDirty || valueEdits.size ? 'dirty' : state ? 'saved' : ''}`;
  $$('[data-stage]').forEach(button => {
    const target = button.dataset.stage;
    button.classList.toggle('active', target === stage);
    button.classList.toggle('current', target === stage);
    button.classList.toggle('complete', target === 'upload' && !!state?.document || target === 'spec' && !!state?.spec?.approved);
    button.disabled = busy || target !== 'upload' && target !== 'batch' && !state?.document || target === 'dat' && (!state?.spec?.approved || specDirty);
    if (button.classList.contains('stage-step')) button.setAttribute('aria-current', target === stage ? 'step' : 'false');
  });
  $('#uploadStage').classList.toggle('hidden', stage !== 'upload');
  $('#reviewStage').classList.toggle('hidden', stage === 'upload' || stage === 'batch');
  $('#batchStage').classList.toggle('hidden', stage !== 'batch');
  renderProvider(); renderJobs(); renderSources();
  if (stage === 'batch') renderBatch();
  if (stage === 'spec' || stage === 'dat') { renderReviewStatus(); renderFileRules(); renderTable(); renderFieldContext(); renderSummary(); renderGeneration(); }
  renderButtons();
}
const batchPath = suffix => projectPath(`/batch${suffix}`);
function acceptBatch(next) {
  batchState = next;
  if (state?.project && next.project) state.project = next.project;
  const specs = next.batch?.specs || [];
  if (!specs.some(spec => spec.id === batchSpecId)) { batchSpecId = specs[0]?.id || null; batchSpecDraft = null; batchSpecDirty = false; batchSpecEditing = false; }
  const dats = next.batch?.dats || [];
  if (!dats.some(dat => dat.id === batchDatId)) { batchDatId = dats[0]?.id || null; batchRecords = []; batchRecord = null; batchRecordDetail = null; batchOffset = 0; batchTotal = 0; }
  render();
}
async function loadBatch() {
  if (!projectId) return;
  acceptBatch(await request(batchPath('')));
}
async function batchAction(path, payload = {}, method = 'POST') {
  if (!batchState) throw Error('Open a batch project first.');
  const next = await request(batchPath(path), {method,body:JSON.stringify({version:batchState.project.version,...payload})});
  acceptBatch(next);
  await listProjects();
  return next;
}
function chosenBatchSpec() { return (batchState?.batch?.specs || []).find(spec => spec.id === batchSpecId); }
function renderBatch() {
  const batch = batchState?.batch;
  $('#batchWorkspace').classList.toggle('hidden', !batch || !(batch.specs?.length || batch.dats?.length));
  $('#batchUploadButton').disabled = !!state?.document || busy;
  $('#batchProjectName').value = state?.project?.name || $('#batchProjectName').value;
  $('#batchProjectName').disabled = !!projectId;
  if (!batch) return;
  const specs = batch.specs || [], dats = batch.dats || [], associations = batch.associations || [];
  const summary = batch.summary || {};
  $('#batchSummary').innerHTML = [[specs.length,'Specifications',''],[dats.length,'DAT files',''],[summary.records ?? summary.record_count ?? '—','Records',''],[summary.unvalidated ?? summary.blocked ?? '—','Unvalidated','warning']].map(([value,label,tone]) => `<div class="summary-card ${tone}"><strong>${esc(value)}</strong><small>${esc(label)}</small></div>`).join('');
  $('#batchSpecList').innerHTML = specs.map(spec => `<div class="batch-file-row"><button class="batch-file ${spec.id===batchSpecId?'selected':''}" type="button" data-batch-spec="${esc(spec.id)}"><strong>${esc(spec.name)}</strong><span class="pill ${spec.approved?'good':'warn'}">${spec.approved?'Approved':'Needs approval'}</span><small>Version ${esc(spec.version)} · ${(spec.fields||[]).length} fields · ${(spec.layouts||[]).length} layouts · ${esc(spec.ai_status||'AI not run')}</small></button><button class="secondary compact" type="button" data-batch-spec-source="${esc(spec.id)}">Original</button></div>`).join('') || '<p class="annotation">Upload a CSV or Excel specification.</p>';
  $('#batchSpecSelect').innerHTML = specs.map(spec => `<option value="${esc(spec.id)}"${spec.id===batchSpecId?' selected':''}>${esc(spec.name)} · v${esc(spec.version)}</option>`).join('');
  const spec = chosenBatchSpec();
  if (!batchSpecDirty) batchSpecDraft = spec ? clone(spec) : null;
  const unknownMandatory = (batchSpecDraft?.fields||[]).filter(field=>field.mandatory==='UNKNOWN'&&['physical','filler'].includes(field.rules?.mapping_type)).length;
  $('#batchSpecStatus').className = `review-status${spec?.approved&&!batchSpecDirty&&!batchSpecEditing?' approved':''}`;
  $('#batchSpecStatus').innerHTML = !spec ? 'Add a specification to begin.' : `<strong>${batchSpecDirty?'Unsaved specification changes':batchSpecEditing?'Revising approved specification; saving requires a new approval':spec.approved?'Approved specification':'Review every field and routing condition before approval'}</strong><p>${unknownMandatory} Unknown mandatory fields · ${(spec.coverage_limitations||[]).length} coverage limitations. ${unknownMandatory?'Acknowledgment does not make these rules fully verified.':''}</p><p>${esc((spec.issues||[]).map(issueText).slice(0,4).join(' · ') || 'Review the source and routing conditions before approval.')}</p>`;
  $('#batchSaveSpec').disabled = !spec || !batchSpecDirty || busy;
  $('#batchEditSpec').classList.toggle('hidden', !spec?.approved || batchSpecEditing);
  $('#batchApproveSpec').disabled = !spec || batchSpecDirty || spec.approved || busy;
  $('#batchAckUnknown').checked = batchSpecDraft?.options?.acknowledge_unknown_mandatory === true;
  $('#batchAckUnknown').disabled = !spec || !!spec.approved && !batchSpecEditing;
  const locked = spec?.approved && !batchSpecEditing;
  $('#batchLayoutRules').innerHTML = (batchSpecDraft?.layouts||[]).map((layout,index) => `<details class="layout-rule"><summary>${esc(layout.name||layout.id)} · ${esc(layout.record_length||'unknown')} bytes · ${(layout.field_ids||[]).length} fields</summary><div class="routing-conditions">${(layout.when||[]).map((condition,j) => `<label>Bytes <input type="number" min="1" data-batch-layout="${index}" data-batch-condition="${j}" data-batch-key="start" value="${esc(condition.start)}" ${locked?'disabled':''}> through <input type="number" min="1" data-batch-layout="${index}" data-batch-condition="${j}" data-batch-key="end" value="${esc(condition.end)}" ${locked?'disabled':''}> equal <input data-batch-layout="${index}" data-batch-condition="${j}" data-batch-key="equals" value="${esc(condition.equals)}" ${locked?'disabled':''}></label>`).join('') || '<p class="annotation">No condition: matches any record. Add an explicit record identifier before using multiple layouts.</p>'}</div>${locked?'':`<button class="secondary compact" type="button" data-batch-add-condition="${index}">Add condition</button>`}</details>`).join('');
  $('#batchSpecTable thead').innerHTML = `<tr>${columns.map(column => `<th scope="col">${esc(column.label)}</th>`).join('')}</tr>`;
  $('#batchSpecTable tbody').innerHTML = (batchSpecDraft?.fields||[]).map((field,index) => `<tr>${columns.map(column => `<td>${column.key==='id'?esc(field.id):column.key==='mandatory'?`<select data-batch-field="${index}" data-batch-column="mandatory" aria-label="Mandatory for ${esc(field.field_name)}" ${locked?'disabled':''}><option value="UNKNOWN"${field.mandatory==='UNKNOWN'?' selected':''}>Unknown</option><option value="YES"${field.mandatory==='YES'?' selected':''}>Yes</option><option value="NO"${field.mandatory==='NO'?' selected':''}>No</option></select>`:`<textarea class="spec-input" data-batch-field="${index}" data-batch-column="${column.key}" aria-label="${esc(column.label)} for ${esc(field.field_name)}" rows="2" ${locked?'disabled':''}>${esc(field[column.key]??'')}</textarea>`}${column.key==='field_name'?`<button class="field-detail-button" type="button" data-batch-rule-index="${index}" ${locked?'disabled':''}>Rules & source ↗</button>`:''}</td>`).join('')}</tr>`).join('');
  $('#batchFileList').innerHTML = dats.map(dat => `<div class="batch-file-row"><button class="batch-file ${dat.id===batchDatId?'selected':''}" type="button" data-batch-dat="${esc(dat.id)}"><strong>${esc(dat.name)}</strong><span class="pill ${dat.export_eligible?'good':'warn'}">${esc(dat.status||'Unprocessed')}</span><small>${esc(dat.record_count ?? '—')} records · ${esc(dat.summary?.validated_count??0)} deterministic passed · ${esc(dat.summary?.unvalidated_count??0)} unresolved · AI ${esc(dat.summary?.ai_status||'not run')} · SHA-256 ${esc((dat.sha256||'').slice(0,16))}…</small></button><button class="secondary compact" type="button" data-batch-original="${esc(dat.id)}">Original</button><button class="secondary compact" type="button" data-batch-download="${esc(dat.id)}" ${dat.export_eligible?'':'disabled'}>Download DAT</button></div>`).join('') || '<p class="annotation">Upload at least one DAT file.</p>';
  $('#batchAssociations').innerHTML = dats.map(dat => `<fieldset class="batch-association"><legend>${esc(dat.name)}</legend>${specs.map(spec => `<div><strong>${esc(spec.name)} · v${esc(spec.version)}</strong>${(spec.layouts||[]).map(layout => {const checked=associations.some(item=>item.dat_id===dat.id&&item.spec_id===spec.id&&(item.layout_ids||[]).includes(layout.id));return `<label class="checkbox-label"><input type="checkbox" data-batch-dat-id="${esc(dat.id)}" data-batch-spec-id="${esc(spec.id)}" data-batch-layout-id="${esc(layout.id)}" ${checked?'checked':''} ${spec.approved?'':'disabled'}> ${esc(layout.name||layout.id)}${spec.approved?'':' · approve first'}</label>`;}).join('')||'<p class="annotation">No layouts defined.</p>'}</div>`).join('')}</fieldset>`).join('');
  $('#batchSaveAssociations').disabled = !dats.length || busy;
  $('#batchProcess').disabled = !dats.length || busy;
  $('#batchAi').disabled = !summary.records || busy;
  $('#batchGenerate').disabled = !dats.some(dat=>dat.summary?.unvalidated_count===0&&dat.summary?.ai_status==='reviewed') || busy;
  $('#batchDownloadArchive').disabled = !dats.some(dat=>dat.export_eligible) || busy;
  $('#batchExportNote').textContent = `${dats.filter(dat=>dat.export_eligible).length} of ${dats.length} DAT files are eligible for export. The archive includes only eligible files and a manifest naming blocked files.${(summary.blocked_files||[]).length?' Blocked: '+summary.blocked_files.map(item=>`${dats.find(dat=>dat.id===item.dat_id)?.name||item.dat_id}: ${item.reason}`).join(' · '):''}`;
  renderBatchRecords();
}
function renderBatchRecords() {
  $('#batchRecordList').innerHTML = batchRecords.length ? batchRecords.map((record,index) => `<button class="batch-file ${batchRecord===index?'selected':''}" type="button" data-batch-record="${index}"><strong>Record ${esc(record.number??index+1)}</strong><span class="pill ${record.status==='validated'?'good':'warn'}">${record.status==='validated'?'Deterministic passed':esc(record.status||'Unvalidated')}</span><small>${esc(record.layout_id||'No layout')} · ${esc(record.spec_id||'No specification')} · AI ${esc(record.ai_status||'not run')} · ${(record.issues||[]).length} findings</small></button>`).join('') : '<p class="annotation">Select a DAT file, then analyze the batch to inspect its records.</p>';
  $('#batchRecordRange').textContent = batchTotal ? `${batchOffset+1}–${batchOffset+batchRecords.length} of ${batchTotal}` : 'No processed records';
  $('#batchPreviousRecords').disabled = batchOffset===0;
  $('#batchNextRecords').disabled = batchOffset+batchRecords.length>=batchTotal;
  const record = batchRecordDetail;
  const field = (record?.fields||[]).find(item=>item.id===batchFieldId);
  $('#batchRecordEvidence').innerHTML = record ? `<div class="evidence-row"><strong>Routing</strong><p>${esc(record.routing_status||record.status||'Unvalidated')} · specification ${esc(record.spec_id||'none')} · version ${esc(record.spec_version??'—')} · layout ${esc(record.layout_id||'none')}</p></div><div class="evidence-row"><strong>Validation</strong><p>${esc(record.validation_status||record.status||'Unvalidated')} · AI ${esc(record.ai_status||'not run')}</p>${(record.issues||[]).map(issue=>`<p class="issue-text">${esc(issueText(issue))}</p>`).join('')}</div><div class="evidence-row"><strong>Alignment</strong><p>${esc(record.alignment?.status||'Not analyzed')}</p>${(record.alignment?.candidates||[]).slice(0,5).map(candidate=>`<p>${esc(candidate.id)} · score ${esc(candidate.score??'—')} · ${candidate.certified?'certified':'review required'}</p>`).join('')}</div><div class="evidence-row"><strong>AI audit</strong><p>${esc(record.ai_review?.summary||'No completed AI audit for this record.')}</p>${(record.ai_review?.findings||[]).map(item=>`<p class="issue-text">${esc(item.field_id)} · ${esc(item.reason||item.message||item.code||'Review finding')}</p>`).join('')}${record.ai_status==='needs_review'?`<label>Disposition<select id="batchAiDecision"><option value="reject">Reject AI advice</option><option value="accept">Accept for review</option><option value="dismiss">Dismiss with reason</option><option value="resolve">Resolved through specification revision</option></select></label><label>Reason<input id="batchAiReason" minlength="3" placeholder="Explain your review decision"></label><button class="secondary compact" id="batchSaveAiDecision" type="button">Save AI review decision</button><p class="annotation">This records your decision. AI cannot change original values or override deterministic validation.</p>`:''}</div><div class="evidence-row"><strong>Byte provenance</strong><p>${record.byte_preserved?'Original record bytes retained':'Certified structural correction applied; inspect alignment ledger.'}</p><code>Original SHA-256: ${esc(record.original_sha256||'—')}\nOutput SHA-256: ${esc(record.output_sha256||'—')}</code></div><div class="evidence-row"><strong>Fields</strong>${(record.fields||[]).map(item=>`<button type="button" class="batch-file ${item.id===batchFieldId?'selected':''}" data-batch-field-evidence="${esc(item.id)}">${esc(item.field_name||item.id)} · ${esc(item.status||'unvalidated')}</button>`).join('')||'<p>Field evidence unavailable for this record.</p>'}</div>${field?`<div class="evidence-row"><strong>${esc(field.field_name||field.id)}</strong><code>Original: ${esc(field.original_value??'—')}\nWorking: ${esc(field.value??'—')}\nOriginal bytes: ${esc(field.original_bytes_hex||'—')}</code>${(field.issues||[]).map(issue=>`<p class="issue-text">${esc(issueText(issue))}</p>`).join('')}</div>`:''}` : 'Select a record to inspect its routing, validation, and source evidence.';
  if (record?.correction && batchDatId) {
    const link = document.createElement('a');
    link.href = batchPath(`/provenance/${encodeURIComponent(batchDatId)}/${record.number}`);
    link.textContent = 'Download byte-level correction provenance';
    link.download = `provenance-${record.number}.json`;
    link.className = 'source-download';
    $('#batchRecordEvidence').append(link);
  }
}
async function loadBatchRecords() {
  if (!batchDatId) return;
  const response = await request(batchPath(`/records/${encodeURIComponent(batchDatId)}?offset=${batchOffset}&limit=100`));
  batchRecords = response.records || response.items || [];
  batchTotal = response.total || 0;
  batchRecord = null;
  batchRecordDetail = null;
  batchFieldId = null;
  renderBatchRecords();
}
async function selectBatchRecord(index) {
  const response = await request(batchPath(`/records/${encodeURIComponent(batchDatId)}?offset=${batchOffset+index}&limit=1`));
  batchRecord = index;
  batchRecordDetail = response.records?.[0] || null;
  batchFieldId = null;
  renderBatchRecords();
}
function openBatchRules(index) {
  const field=batchSpecDraft?.fields?.[index];if(!field)return;
  batchRuleIndex=index;
  const rules=field.rules||{}, option=(values,current)=>values.map(value=>`<option value="${esc(value)}"${value===current?' selected':''}>${esc(value)}</option>`).join('');
  $('#batchRuleTitle').textContent=field.field_name||field.id;
  $('#batchRuleContent').innerHTML=`<div class="rule-grid"><label>Mapping type<select id="batchRuleMapping">${option(['physical','filler','constant','system'],rules.mapping_type||'physical')}</select></label><label>Value type<select id="batchRuleDatatype">${option(['char','integer','decimal','float','date','unknown'],rules.datatype||'unknown')}</select></label><label>Conversion<select id="batchRuleConversion">${option(['identity','trim','rtrim','ltrim','to_number','to_date','unsupported','database'],rules.conversion||'identity')}</select></label><label>Padding side<select id="batchRulePadding">${option(['right','left','none','unknown'],rules.padding?.side||'unknown')}</select></label><label>Padding byte<input id="batchRulePadChar" maxlength="1" value="${esc(rules.padding?.char??' ')}"></label><label>Date format<input id="batchRuleDateFormat" value="${esc(rules.date_format||'')}"></label><label>Explicit constant<input id="batchRuleConstant" value="${esc(rules.constant??'')}"></label><label>Allowed values, one per line<textarea id="batchRuleAllowed" rows="3">${esc((rules.allowed_values||[]).join('\n'))}</textarea></label><label>Mandatory<select id="batchRuleMandatory">${option(['UNKNOWN','YES','NO'],field.mandatory||'UNKNOWN')}</select></label><label class="checkbox-label"><input id="batchRuleInternalSpaces" type="checkbox" ${rules.allow_internal_whitespace!==false?'checked':''}> Allow internal spaces</label></div><div class="detail-section"><h3>Original source</h3><p>${esc(field.source_reference||'No source reference')}</p><code>${esc(JSON.stringify(field.source?.extra_columns||{}))}</code></div>`;
  $('#batchRuleDialog').showModal();
}
function saveBatchRules() {
  const field=batchSpecDraft?.fields?.[batchRuleIndex];if(!field)return;
  const pad=$('#batchRulePadChar').value;
  if(!/^[\x20-\x7e]$/.test(pad)){notify('Padding must be one printable ASCII byte.',true);return;}
  const rules=field.rules||{};
  Object.assign(rules,{mapping_type:$('#batchRuleMapping').value,datatype:$('#batchRuleDatatype').value,conversion:$('#batchRuleConversion').value,padding:{...(rules.padding||{}),side:$('#batchRulePadding').value,char:pad},date_format:$('#batchRuleDateFormat').value,constant:$('#batchRuleConstant').value,allowed_values:$('#batchRuleAllowed').value.split('\n').filter(Boolean),allow_internal_spaces:$('#batchRuleInternalSpaces').checked,allow_internal_whitespace:$('#batchRuleInternalSpaces').checked});
  field.mandatory=$('#batchRuleMandatory').value;rules.required=field.mandatory==='YES'?true:field.mandatory==='NO'?false:null;field.rules=rules;
  batchSpecDirty=true;$('#batchRuleDialog').close();renderBatch();
}
function renderProvider() {
  const ai = state?.ai_status;
  if (!ai) return;
  $('#providerSummary').textContent = `Bedrock · ${ai.ai_available === false ? 'unavailable' : ai.model_id || 'not configured'}`;
}
function renderJobs() {
  const latest = new Map();
  for (const job of state?.jobs || []) if (!latest.has(job.kind)) latest.set(job.kind, job);
  const jobs = [...latest.values()].filter(job => ['queued','running','retry_wait','failed'].includes(job.status));
  $('#jobPanel').classList.toggle('hidden', !jobs.length);
  $('#jobPanel').innerHTML = jobs.map(job => {
    const running = ['queued','running','retry_wait'].includes(job.status);
    const title = job.kind === 'dat' ? 'DAT analysis' : 'Specification interpretation';
    const progress = job.total_batches ? ` · ${job.completed_batches || 0} of ${job.total_batches} groups complete` : '';
    const label = {queued:'Queued',running:'In progress',retry_wait:'Waiting for provider',failed:'Needs attention'}[job.status] || job.status;
    return `<div class="job-row ${job.status === 'failed' ? 'failed' : ''}">${running ? '<span class="spinner" aria-hidden="true"></span>' : '<span aria-hidden="true">!</span>'}<strong>${esc(title)} · ${esc(label)}${esc(progress)}</strong><span class="job-message">${esc(job.message || (job.status === 'queued' ? 'Your work is saved while this request waits.' : ''))}${job.error ? `<br><span class="job-error">${esc(errorMessage(job.error))}</span>` : ''}</span></div>`;
  }).join('');
}
function renderSources() {
  const document = state?.document;
  $('#uploadedSources').classList.toggle('hidden', !document);
  if (document) $('#uploadedSources').innerHTML = `<h2>Saved source files</h2><div class="source-summary"><div><strong>${esc(document.name || document.filename || 'Specification')}</strong><p>${state.spec?.fields?.length || 0} interpreted fields · original document preserved</p></div><div><strong>Original DAT</strong><p>${state.review?.record_count ?? 'Not yet analyzed'} physical records · edits do not alter the original</p></div></div><button class="secondary" data-stage="${state.spec?.approved ? 'dat' : 'spec'}">Continue review →</button>`;
}
function renderReviewStatus() {
  const spec = state.spec || {};
  const approved = !!spec.approved && !specDirty;
  $('#reviewEyebrow').textContent = stage === 'dat' ? 'DAT REVIEW' : 'SPECIFICATION REVIEW';
  $('#reviewTitle').textContent = stage === 'dat' ? 'Review values against the approved spec' : 'Confirm the field specification';
  $('#reviewDescription').textContent = stage === 'dat' ? 'Keep source values in view. Enter data only; the approved rules apply padding and formatting.' : 'Review every master-template attribute. Unknown mandatory rules remain visible coverage limits.';
  $('#specStatus').className = `review-status${approved ? ' approved' : ''}`;
  const issues = [...(spec.issues || []), ...(spec.ai_questions || [])];
  const intro = specDirty ? 'You have unsaved specification changes. Saving changes invalidates the previous approval and prepared download.' : approved ? `Specification approved · version ${spec.version || 'saved'}. ${stage === 'dat' ? 'Changing the specification requires a new review and approval.' : 'You can continue to DAT review or edit the draft.'}` : spec.ai_complete ? 'AI interpretation is complete. Review every field and the rules behind it before approving.' : 'AI interpretation is required before approval. Start interpretation, then review the resulting fields and rules.';
  const unknownCount = draftFields.filter(field => field.mandatory === 'UNKNOWN' && ['physical','filler'].includes(field.rules?.mapping_type)).length;
  $('#specStatus').innerHTML = `<strong>${esc(intro)}</strong>${unknownCount ? `<p>${unknownCount} physical fields have Unknown mandatory status. ${draftOptions.acknowledge_unknown_mandatory ? 'Acknowledged coverage limitation; the DAT cannot be called fully validated until these rules are resolved.' : 'Acknowledge this limitation or set each status before approval.'}</p>` : ''}${spec.ai_summary ? `<p>${esc(spec.ai_summary)}</p>` : ''}${issues.length ? `<ul>${issues.slice(0,6).map(issue => `<li>${esc(issueText(issue))}</li>`).join('')}</ul>${issues.length > 6 ? `<p>${issues.length - 6} additional items are available in field details.</p>` : ''}` : ''}`;
  if(stage === 'dat'){
    const review=state.review?.record, recordIssues=review?.issues||[];
    const uncertain=recordIssues.some(issue=>issue.code==='UNCERTAIN_BOUNDARIES');
    const unknown=recordIssues.some(issue=>issue.code==='UNKNOWN_LAYOUT'||issue.code==='AMBIGUOUS_LAYOUT');
    $('#specStatus').innerHTML+=`<p><strong>${state.ai_review?.complete ? 'DAT AI review complete.' : 'DAT AI analysis is required before download.'}</strong> ${esc(state.ai_review?.summary||'Select Analyze DAT to start the review.')}</p>${recordIssues.length?`<ul>${recordIssues.slice(0,5).map(issue=>`<li>${esc(issueText(issue))}</li>`).join('')}</ul>`:''}${uncertain?`<button class="row-action" data-confirm-boundary="${review.number}">Confirm reviewed source boundaries</button>`:''}${unknown&&review.status!=='preserved'?'<p>Unrecognized records are blocked. Review the routing rules below; explicitly preserving unknown records requires a revised specification and approval.</p>':''}`;
  }
  $('#recordTools').classList.toggle('hidden', stage !== 'dat');
  $('#bulkSpecTools').classList.toggle('hidden', stage !== 'spec');
  $('#ackUnknownMandatory').checked = draftOptions.acknowledge_unknown_mandatory === true;
  $('#issueFilterLabel').classList.toggle('hidden', stage !== 'dat');
  $('#datActions').classList.toggle('hidden', stage !== 'dat');
  $('#recordNumber').value = state.review?.record?.number || 1;
  $('#recordNumber').max = state.review?.record_count || 1;
  $('#recordTotal').textContent = `of ${state.review?.record_count || 0}`;
  $('#tableHint').textContent = stage === 'spec' ? 'Tab / Enter moves between cells. Save the draft before approval. Position changes never renumber other fields.' : 'Enter data without boundary padding. Tab / Enter moves between values. Original values remain read-only.';
  $('#pendingEdits').textContent = specDirty ? 'Specification draft changed' : valueEdits.size ? `${valueEdits.size} value change(s) to save` : 'All changes saved';
}
function renderSummary() {
  $('#summaryCards').classList.toggle('hidden', stage !== 'dat');
  if (stage !== 'dat') return;
  const summary = state.review?.summary || {};
  const cards = [
    [state.review?.record_count || 0, 'Records in file', ''],
    [summary.percentage === null || summary.percentage === undefined ? '—' : `${Math.round(summary.percentage)}%`, 'Evaluable spec checks passed', 'good'],
    [summary.blocking_count ?? summary.pending_count ?? '—', 'Blocking / unresolved items', 'warning'],
    [summary.changed_records ?? 0, 'Records with saved changes', '']
  ];
  $('#summaryCards').innerHTML = cards.map(([number,label,className]) => `<div class="summary-card ${className}"><strong>${esc(number)}</strong><small>${esc(label)}</small></div>`).join('');
}
function renderFileRules() {
  const editable=stage==='spec', disabled=editable?'':' disabled';
  const options=(choices,current)=>choices.map(([value,label])=>`<option value="${esc(value)}"${value===current?' selected':''}>${esc(label)}</option>`).join('');
  $('#fileRulesContent').innerHTML=`<div class="rules-option-grid"><label>DAT encoding<select data-option="encoding"${disabled}>${options([['ascii','ASCII'],['latin1','ISO-8859-1'],['cp1252','Windows-1252'],['utf-8','UTF-8 · byte positions apply']],draftOptions.encoding||'ascii')}</select></label><label>Physical record format<select data-option="record_format"${disabled}>${options([['stream','Line-separated records'],['fixed','Fixed byte count, no separators']],draftOptions.record_format||'stream')}</select></label><label>Fixed record bytes, if applicable<input type="number" min="1" max="1000000" data-option="record_length" value="${esc(draftOptions.record_length??'')}"${disabled}></label><label>Unrecognized records<select data-option="unknown_records"${disabled}>${options([['block','Block download until resolved'],['preserve','Preserve original bytes · explicitly approved']],draftOptions.unknown_records||'block')}</select></label></div><p class="annotation">An empty routing condition matches every record. For mixed files, specify which byte values identify each layout. Preserved unrecognized records keep their original bytes and are excluded from field validation.</p><div class="layout-rules">${draftLayouts.map((layout,index)=>`<div class="layout-rule"><div class="layout-heading"><label>Layout name<input data-layout-index="${index}" data-layout-key="name" value="${esc(layout.name||layout.id)}"${disabled}></label><label>Record width in bytes<input type="number" min="1" max="1000000" data-layout-index="${index}" data-layout-key="record_length" value="${esc(layout.record_length||'')}"${disabled}></label><span class="annotation">${(layout.field_ids||[]).length} fields · ${esc(layout.id)}</span></div><div class="routing-conditions">${(layout.when||[]).map((condition,conditionIndex)=>`<div class="routing-condition"><span>When bytes</span><input type="number" min="1" data-layout-index="${index}" data-condition-index="${conditionIndex}" data-condition-key="start" value="${esc(condition.start??'')}" aria-label="Routing start byte for ${esc(layout.name||layout.id)}"${disabled}><span>through</span><input type="number" min="1" data-layout-index="${index}" data-condition-index="${conditionIndex}" data-condition-key="end" value="${esc(condition.end??'')}" aria-label="Routing end byte for ${esc(layout.name||layout.id)}"${disabled}><span>equal</span><input data-layout-index="${index}" data-condition-index="${conditionIndex}" data-condition-key="equals" value="${esc(condition.equals??'')}" aria-label="Routing value for ${esc(layout.name||layout.id)}"${disabled}>${editable?`<button class="text-button" data-remove-condition="${index}:${conditionIndex}" aria-label="Remove routing condition">Remove</button>`:''}</div>`).join('')||'<p class="annotation">No condition · this layout matches all records.</p>'}${editable?`<button class="secondary compact" data-add-condition="${index}">Add routing condition</button>`:''}</div><details><summary>Fields in this layout</summary><p class="annotation">${(layout.field_ids||[]).map(id=>esc(draftFields.find(field=>field.id===id)?.field_name||id)).join(' · ')}</p></details></div>`).join('')}</div><p class="annotation">Change a field’s layout in Rules & source. Save the specification draft to validate these rules before approval.</p>`;
}
function renderTable() {
  const datMode = stage === 'dat';
  $('#reviewTable').classList.toggle('dat-mode', datMode);
  $('#reviewHead').innerHTML = `<tr>${columns.map(column => `<th scope="col" class="${column.className}"${['raw_sample','cleansed_output'].includes(column.key) ? ' title="Specification example only; this is not a replacement for actual DAT values"' : ''}>${esc(column.label)}</th>`).join('')}${datMode ? '<th class="value-col" scope="col">Original DAT value</th><th class="value-col" scope="col">Value to use</th><th class="match-col" scope="col">Spec match</th><th class="action-col" scope="col">Issue / action</th>' : ''}</tr>`;
  const search = $('#fieldSearch').value.toLowerCase();
  const wrap=$('.review-table-wrap'), scrollLeft=wrap.scrollLeft, scrollTop=wrap.scrollTop;
  const focused=document.activeElement;
  const focus=focused?.matches('[data-value-field],[data-spec-field]')?{field:focused.dataset.valueField||focused.dataset.specField,column:focused.dataset.column,start:focused.selectionStart,end:focused.selectionEnd}:null;
  const fields = draftFields.filter(field => {
    if (search && !`${field.field_name} ${field.category_functional_group}`.toLowerCase().includes(search)) return false;
    if (datMode && $('#issuesOnly').checked) {
      const result = reviewField(field.id);
      if (result && ['passed','valid','matched'].includes(result.status) && !result.issues?.length) return false;
    }
    return true;
  });
  $('#fieldCount').textContent = `${fields.length} of ${draftFields.length} fields`;
  $('#tableEmpty').classList.toggle('hidden', !!fields.length);
  $('#reviewBody').innerHTML = fields.map(field => {
    const result = datMode ? reviewField(field.id) : null;
    const issues = datMode ? result?.issues || [] : fieldIssues(field);
    return `<tr data-field-row="${esc(field.id)}" class="${issues.length ? 'issue-row' : ''}">${columns.map(column => {
      const text = field[column.key] ?? '';
      const content = datMode || column.key === 'id' ? `<span class="${column.key === 'field_name' ? 'field-name-read' : ''}">${esc(text)}</span>` : column.key === 'mandatory' ? `<select class="spec-input" data-spec-field="${esc(field.id)}" data-column="mandatory" aria-label="Mandatory for ${esc(field.field_name)}"><option value="UNKNOWN"${text==='UNKNOWN'?' selected':''}>Unknown</option><option value="YES"${text==='YES'?' selected':''}>Yes</option><option value="NO"${text==='NO'?' selected':''}>No</option></select>` : `<textarea class="spec-input${column.key === 'field_name' ? ' field-name' : ''}${issues.length ? ' invalid' : ''}" data-spec-field="${esc(field.id)}" data-column="${column.key}" aria-label="${esc(column.label)} for ${esc(field.field_name)}" rows="2" spellcheck="false"${activeJobs().some(job=>job.kind==='spec')?' disabled':''}>${esc(text)}</textarea>`;
      return `<td class="${column.className}">${content}${column.key === 'field_name' ? `<button class="field-detail-button" data-details="${esc(field.id)}">Rules & source ↗</button>` : ''}</td>`;
    }).join('')}${datMode ? renderDatCells(field,result) : ''}</tr>`;
  }).join('');
  wrap.scrollLeft=scrollLeft;wrap.scrollTop=scrollTop;
  if(focus){const input=$$('[data-value-field],[data-spec-field]').find(item=>(item.dataset.valueField||item.dataset.specField)===focus.field&&item.dataset.column===focus.column);if(input){input.focus({preventScroll:true});if(input.setSelectionRange)input.setSelectionRange(focus.start,focus.end);}}
  if (pendingFocus) { const target = $$('[data-value-field]').find(input => input.dataset.valueField === pendingFocus); target?.focus(); pendingFocus = null; }
}
function renderDatCells(field, result) {
  if (!result) return '<td class="value-col"><span class="value-label">Not applicable to this layout</span></td><td class="value-col">—</td><td class="match-col"><span class="pill neutral">Not applicable</span></td><td class="action-col"><span class="value-label">No source field in this record.</span></td>';
  const physical = !['system','constant','filler'].includes(field.rules?.mapping_type);
  const edited = valueEdits.has(`${state.review.record.number}:${field.id}`);
  const known = result.percentage !== null && result.percentage !== undefined;
  const pass = known && result.percentage === 100 && !result.issues?.length;
  const matchLabel=result.status==='not_applicable'?'Not applicable':known?`${Math.round(result.percentage)}%`:result.status==='needs_review'?'Needs review':'Not verified';
  const issues = result.issues || [];
  const ambiguous = result.status === 'ambiguous' || result.boundaries_uncertain || (state.review?.record?.issues||[]).some(issue=>issue.code==='UNCERTAIN_BOUNDARIES');
  const value = currentValue(result);
  return `<td class="value-col"><div class="original-value">${result.original_value === null || result.original_value === undefined ? 'No source reading' : result.original_value === '' ? '(blank)' : esc(result.original_value)}</div>${ambiguous ? '<span class="pill warn">Boundary needs review</span>' : '<span class="value-label">Original source · read-only</span>'}</td><td class="value-col">${physical ? `<input class="value-input${edited ? ' changed' : ''}" data-value-field="${esc(field.id)}" aria-label="Value to use for ${esc(field.field_name)} in record ${state.review.record.number}" value="${esc(value)}" spellcheck="false" autocomplete="off"><span class="value-label">${value === '' ? field.rules?.required ? 'Required value is blank' : 'Blank value' : edited ? 'Unsaved change' : result.value !== result.original_value ? 'Saved working value' : 'Data only · padding applied by spec'}</span>` : `<span class="value-label">${field.rules?.mapping_type === 'filler' ? 'Reserved bytes · system managed' : 'Generated from the approved specification'}</span>`}</td><td class="match-col"><span class="pill ${pass ? 'good' : known ? 'warn' : 'neutral'}">${esc(matchLabel)}</span><span class="check-count">${result.passed ?? 0} / ${result.total ?? 0} applicable checks</span><button class="check-button" data-details="${esc(field.id)}">View checks</button>${edited ? '<span class="value-label">Rechecked after save</span>' : ''}</td><td class="action-col">${issues.length ? issues.map(issue => `<p class="issue-text">${esc(issueText(issue))}</p>`).join('') : `<p class="issue-text resolved">${pass ? 'Matches the supported spec checks.' : 'Review analysis coverage and rules.'}</p>`}${ambiguous ? `<button class="row-action" data-confirm-boundary="${state.review.record.number}">Confirm source reading</button>` : ''}${physical && issues.length ? `<button class="row-action" data-focus-value="${esc(field.id)}">Edit value</button>` : ''}</td>`;
}
function renderFieldContext() {
  const panel = $('#fieldContext');
  if (stage === 'upload') return;
  const field = draftFields.find(item => item.id === selectedFieldId) || draftFields[0];
  if (!field) { panel.textContent = 'Select a field to inspect its source and validation evidence.'; return; }
  const result = reviewField(field.id);
  const alignment = state.review?.record?.alignment;
  const initial = alignment?.initial?.find(item => item.field_id === field.id);
  const mappings = (alignment?.candidates || []).flatMap(candidate => (candidate.fields || []).filter(item => item.id === field.id).map(item => ({...item,candidate_id:candidate.id,certified:candidate.certified})));
  panel.innerHTML = `<h3>${esc(field.field_name || field.id)}</h3><div class="annotation">Field ID ${esc(field.id)} · expected bytes ${esc(field.start_pos || '—')}–${esc(field.end_pos || '—')}</div><div class="evidence-row"><strong>Source reference</strong><div>${esc(field.source_reference || `${field.source?.sheet || ''}!${field.source?.row || ''}`)}</div><strong>Mandatory</strong><div>${esc(field.mandatory || 'UNKNOWN')}</div></div>${stage === 'dat' ? `<div class="evidence-row"><strong>Original bytes</strong><code>${esc(result?.original_bytes_hex || 'No source slice')}</code><strong>Original value</strong><code>${esc(result?.original_value ?? '(none)')}</code></div><div class="evidence-row"><strong>Alignment</strong><div class="pill ${alignment?.status === 'already_valid' ? 'good' : 'warn'}">${esc(alignment?.status || 'Not analyzed')}</div><p>${esc(alignment?.reason || 'Select an applicable physical record.')}</p>${initial ? `<div>Initial source offsets ${esc(initial.start)}–${esc(initial.end)} · ${initial.valid ? 'checks passed' : 'checks need review'}</div>` : ''}${mappings.map(item => `<div>${esc(item.candidate_id)}: source offsets ${esc(item.source_start ?? '—')}–${esc(item.source_end ?? '—')}${item.certified ? ' · structurally certified' : ''}</div>`).join('')}</div><div class="evidence-row"><strong>Validation findings</strong>${(result?.issues || []).map(item => `<p class="issue-text">${esc(issueText(item))}</p>`).join('') || '<p>No field finding in this record.</p>'}</div>` : `<div class="evidence-row"><strong>Source metadata</strong><code>${esc(JSON.stringify(field.source?.extra_columns || {}))}</code></div>`}`;
  if (stage === 'dat' && alignment) {
    const findings = (alignment.field_findings || []).filter(item => item.field_id === field.id);
    const anchors = (alignment.anchor_evidence || []).filter(item => item.fieldId === field.id);
    const score = (alignment.score_breakdown || []).find(item => item.field_id === field.id);
    panel.innerHTML += `<div class="evidence-row"><strong>Structural assessment</strong><div>${esc(alignment.structural_status || 'Review required')} · ${esc(alignment.validation_status || 'Incomplete')}</div><strong>Certification</strong><div>${esc(alignment.certification?.level || 'Human review required')}${alignment.resource_limit_reached ? ' · search limit reached' : ''}</div>${anchors.map(item => `<p>Anchor ${esc(item.anchorType)} at bytes ${esc(item.sourceStart)}–${esc(item.sourceEnd)}</p>`).join('')}${findings.map(item => `<p class="issue-text">${esc(item.code)}: ${esc(item.message)}</p>`).join('')}${score ? `<p>Ranking factors: ${esc(JSON.stringify(score))}</p>` : ''}</div>`;
  }
  if (stage === 'dat') {
    const record = state.review?.record?.number;
    const audit = (state.ai_review?.alignment_reviews || []).find(item => item.record === record);
    if (audit) {
      const finding = audit.reconciliation;
      const decided = (state.ai_decisions || []).some(item => item.evidence_id === audit.evidence_id && item.record === record && item.input_fingerprint === state.ai_review?.input_fingerprint);
      panel.innerHTML += `<div class="evidence-row"><h3>Bedrock audit</h3><p>${esc(finding.summary)}</p><p>Status: ${esc(finding.ai_status)} · ${finding.human_review_required ? 'Human review required' : 'No disagreement recorded'}</p>${finding.findings.map(item => `<p class="issue-text">${esc(item.field_id)}: ${esc(item.concern)}</p>`).join('')}${finding.spec_changes_for_review.map(item => `<p class="issue-text">Proposed spec change · ${esc(item.field_id)}: ${esc(item.attribute)} → ${esc(item.proposed_value)}. ${esc(item.reason)}</p>`).join('')}${finding.rule_proposals_for_review.map(item => `<p class="issue-text">Proposed rule · ${esc(item.field_id)}: ${esc(item.kind)} → ${esc(item.value)}. ${esc(item.reason)}</p>`).join('')}${finding.human_review_required && !decided ? `<button class="secondary compact" data-reject-ai="${esc(audit.evidence_id)}" data-ai-record="${record}">Reject AI advice with reason</button><p class="annotation">To accept a proposed specification change, edit and reapprove the specification.</p>` : decided ? '<span class="pill good">Reviewed and rejected</span>' : ''}</div>`;
    }
  }
}
function renderGeneration() {
  if (stage !== 'dat') return;
  const generation = state.generation || state.export || {};
  const available = !!state.generated?.checksum && !valueEdits.size && !specDirty;
  $('#downloadDat').classList.toggle('hidden', !available || !!valueEdits.size || specDirty);
  $('#generateButton').classList.toggle('hidden', available && !valueEdits.size && !specDirty);
  const blockers = generation.blockers || state.review?.blockers || [];
  const message = available ? 'The current DAT bytes passed the required checks. Download is ready.' : blockers.length ? blockers.map(issueText).join(' ') : state.spec?.approved ? 'Save changes, complete DAT analysis and validate the exact output before download.' : 'Approve the specification to continue.';
  $('#generationStatus').className = available ? 'generation-success' : 'generation-blocked';
  $('#generationStatus').textContent = message;
}
function semanticSpecChanges(){
  const saved=new Map((state?.spec?.fields||[]).map(field=>[field.id,field]));
  return specDirty&&draftFields.some(field=>['datatype_conversion','padding_alignment','mapping_type'].some(key=>field[key]!==saved.get(field.id)?.[key]));
}
function renderButtons() {
  const exists = !!state?.document;
  const pending = activeJobs();
  const specRunning = pending.some(job => job.kind === 'spec');
  const datRunning = pending.some(job => job.kind === 'dat');
  const showSpec = stage === 'spec';
  for (const id of ['saveSpecButton','analyzeSpecButton','approveSpecButton']) $('#'+id).classList.toggle('hidden', !showSpec);
  $('#editSpecButton').classList.toggle('hidden', stage !== 'dat');
  $('#analyzeDatButton').classList.toggle('hidden', stage !== 'dat');
  $('#saveSpecButton').disabled = busy || !exists || !specDirty;
  $('#analyzeSpecButton').disabled = busy || !exists || specRunning || !!state?.spec?.approved&&!specDirty;
  $('#approveSpecButton').disabled = busy || !draftFields.length || specRunning || !state?.spec?.ai_complete || semanticSpecChanges();
  $('#approveSpecButton').textContent = state?.spec?.approved && !specDirty ? 'Continue to DAT review →' : 'Approve specification →';
  $('#analyzeDatButton').disabled = busy || !state?.spec?.approved || datRunning;
  $('#analyzeDatButton').textContent = datRunning ? 'Analysis in progress…' : 'Analyze DAT';
  $('#undoButton').disabled = busy || !(state?.undo_count || state?.history?.can_undo);
  $('#redoButton').disabled = busy || !(state?.redo_count || state?.history?.can_redo);
  $('#saveValuesButton').disabled = busy || !valueEdits.size;
  $('#generateButton').disabled = busy || !state?.spec?.approved || datRunning;
  $('#previousRecord').disabled = busy || (state?.review?.record?.number || 1) <= 1;
  $('#nextRecord').disabled = busy || (state?.review?.record?.number || 1) >= (state?.review?.record_count || 0);
  $('#nextIssue').disabled = busy || !(state?.review?.record_count);
  $('#refreshProject').disabled = busy || !projectId;
  $('#newProjectButton').disabled = busy;
  $('#uploadButton').disabled = busy || !!state?.document || state?.stage==='legacy' || !!(batchState?.batch?.specs?.length || batchState?.batch?.dats?.length);
}
async function saveSpec() {
  if (!specDirty) return;
  const fields = clone(draftFields), options = clone(draftOptions);
  const next = await request(projectPath('/spec'), {method:'PUT',body:JSON.stringify({version:state.project.version,fields,options,layouts:clone(draftLayouts)})});
  specDirty = false;
  applyState(next);
  notify('Specification draft saved. Review any unresolved rules before approval.');
}
async function saveValues() {
  if (!valueEdits.size) return;
  const edits = Array.from(valueEdits.values());
  const next = await request(projectPath('/values'), {method:'PATCH',body:JSON.stringify({version:state.project.version,edits})});
  valueEdits.clear(); applyState(next);
  notify('Value changes saved. Formatting and checks have been recalculated.');
}
async function changeRecord(number) {
  if (!Number.isInteger(number) || number < 1 || number > (state.review?.record_count || 0)) throw Error('Enter a record number within this file.');
  await saveValues();
  await refreshProject(number);
}
async function nextIssue() {
  const current = state.review?.record?.number || 1;
  const summaries = state.review?.record_summaries || [];
  const unresolved = summaries.filter(record => !['valid','passed','matched'].includes(record.status) || record.issues?.length);
  const next = unresolved.find(record => record.number > current) || unresolved[0];
  if (next) await changeRecord(next.number);
  else notify('No remaining record issues were found in the current review.');
  const first = $$('[data-value-field]').find(input => input.closest('tr').classList.contains('issue-row'));
  first?.focus();
}
function openDetails(id) {
  const field = draftFields.find(item => item.id === id); if (!field) return;
  selectedFieldId = id;
  const rules = field.rules || {}, result = reviewField(id), editable = stage === 'spec';
  $('#detailTitle').textContent = field.field_name || 'Unnamed field';
  const source = field.source || {};
  const sources = Array.isArray(source) ? source : [source];
  const ruleOptions = (values, selected) => values.map(([value,label]) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`).join('');
  $('#detailContent').innerHTML = `<div class="detail-section"><h3>Reviewed formatting & validation rules</h3><p>${editable ? 'These controls describe how the field is read and written. Rule changes are saved with the specification draft.' : 'These rules come from the approved specification. Edit the specification to change them.'}</p><div class="rule-grid"><label>Record layout<input id="ruleLayout" list="layoutChoices" value="${esc(rules.layout_id||draftLayouts.find(layout=>(layout.field_ids||[]).includes(field.id))?.id||'primary')}"${editable?'':' disabled'}><datalist id="layoutChoices">${draftLayouts.map(layout=>`<option value="${esc(layout.id)}">${esc(layout.name||layout.id)}</option>`).join('')}</datalist><span class="annotation">Choose a layout or enter a new layout name.</span></label><label>Field mapping<select id="ruleMapping"${editable ? '' : ' disabled'}>${ruleOptions([['physical','Value from DAT'],['filler','Reserved / filler'],['constant','Constant from spec'],['system','System generated']],rules.mapping_type || 'physical')}</select></label><label>Value type<select id="ruleDatatype"${editable ? '' : ' disabled'}>${ruleOptions([['char','Text'],['integer','Whole number'],['decimal','Decimal number'],['float','Scientific / decimal number'],['date','Date'],['unknown','Unresolved type']],rules.datatype || 'char')}</select></label><label>Padding from specification<select id="rulePadding"${editable ? '' : ' disabled'}>${ruleOptions([['right','Right padding · left aligned'],['left','Left padding · right aligned'],['none','No padding'],['unknown','Unresolved padding']],rules.padding?.side || 'right')}</select></label><label>Padding character<input id="rulePadChar" maxlength="1" value="${esc(rules.padding?.char ?? ' ')}"${editable ? '' : ' disabled'}><span class="annotation">A space appears as an empty character.</span></label><label>Supported conversion<select id="ruleConversion"${editable ? '' : ' disabled'}>${ruleOptions([['identity','Keep value'],['trim','Trim boundary padding'],['rtrim','Trim right padding'],['ltrim','Trim left padding'],['unsupported','Unsupported conversion'],['database','Database-only expression'],['to_number','Number preview'],['to_date','Date preview']],rules.conversion || 'identity')}</select></label><label>Date format<input id="ruleDateFormat" value="${esc(rules.date_format || '')}" placeholder="YYYYMMDD"${editable ? '' : ' disabled'}></label><label>Allowed values, one per line<textarea id="ruleAllowed" rows="3"${editable ? '' : ' disabled'}>${esc((rules.allowed_values || []).join('\n'))}</textarea></label><label>Explicit constant<input id="ruleConstant" value="${esc(rules.constant ?? '')}"${editable ? '' : ' disabled'}></label><label class="checkbox-label"><input type="checkbox" id="ruleInternalSpaces"${rules.allow_internal_whitespace!==false?' checked':''}${editable?'':' disabled'}> Allow internal spaces</label><label>Mandatory status<select id="ruleRequired"${editable ? '' : ' disabled'}>${ruleOptions([['UNKNOWN','Unknown'],['YES','Yes'],['NO','No']],field.mandatory||'UNKNOWN')}</select></label></div><p class="annotation">Raw and cleansed samples illustrate formatting. They authorize a constant only when the approved rule explicitly says so. Staging values are local previews, not database results.</p></div>${result ? `<div class="detail-section"><h3>Current record checks</h3>${result.staging_preview!==undefined?`<p><strong>Local staging preview:</strong> ${esc(result.staging_preview??'(blank)')}<br><span class="annotation">${esc(result.preview_label||'Local preview only; not an Oracle database result.')}</span></p>`:''}<ul class="check-list">${(result.checks || []).map(check => `<li><span>${esc(check.label || check.name || check.kind || check.code || 'Spec check')}<small>${esc(check.message || check.reason || '')}</small></span><span class="pill ${check.passed === true || check.status === 'passed' ? 'good' : check.passed === false || check.status === 'failed' ? 'warn' : 'neutral'}">${check.passed === true || check.status === 'passed' ? 'Passed' : check.passed === false || check.status === 'failed' ? 'Needs correction' : 'Not verified'}</span></li>`).join('') || '<li>No evaluable checks are available for this field.</li>'}</ul>${result.original_percentage !== undefined ? `<p>Original source match: ${result.original_percentage === null ? 'Not fully verified' : `${result.original_percentage}%`}. Current checks reflect saved edits and formatting.</p>` : ''}</div>` : ''}<div class="detail-section"><h3>Source provenance</h3><a href="${esc(projectPath('/download?spec_source=true'))}" class="source-download">Download original specification ↗</a>${sources.map(item => `<div class="source-item">${esc(item.section || item.sheet || state.document?.name || 'Uploaded specification')}${item.row ? ` · row ${esc(item.row)}` : ''}${item.line ? ` · line ${esc(item.line)}` : ''}${item.cells ? `<br>${Object.entries(item.cells).map(([key,value]) => `${esc(key)}: ${esc(value)}`).join('<br>')}` : ''}</div>`).join('')}<p class="annotation">The original uploaded document is preserved. Your interpreted specification is stored separately.</p></div>`;
  $('#saveRulesButton').classList.toggle('hidden', !editable);
  $('#detailDialog').showModal();
}
function applyRuleEdits() {
  const field = draftFields.find(item => item.id === selectedFieldId); if (!field) return;
  const rules = clone(field.rules || {});
  rules.mapping_type = $('#ruleMapping').value; rules.datatype = $('#ruleDatatype').value;
  rules.layout_id=$('#ruleLayout').value.trim()||'primary';
  rules.allow_internal_whitespace=$('#ruleInternalSpaces').checked;
  rules.padding = {side:$('#rulePadding').value,char:$('#rulePadChar').value || ' '};
  rules.conversion = $('#ruleConversion').value; rules.date_format = $('#ruleDateFormat').value;
  field.mandatory = $('#ruleRequired').value;
  rules.required = field.mandatory === 'YES' ? true : field.mandatory === 'NO' ? false : null;
  const allowed=$('#ruleAllowed').value.split('\n').map(x=>x.trim()).filter(Boolean);
  if(allowed.length)rules.allowed_values=allowed;else delete rules.allowed_values;
  if (rules.mapping_type === 'constant') rules.constant = $('#ruleConstant').value;
  else delete rules.constant;
  field.rules = rules;
  for(const layout of draftLayouts)layout.field_ids=(layout.field_ids||[]).filter(id=>id!==field.id);
  let layout=draftLayouts.find(item=>item.id===rules.layout_id);
  if(!layout){layout={id:rules.layout_id,name:rules.layout_id,field_ids:[],record_length:rules.end||Number(field.end_pos)||1,when:[]};draftLayouts.push(layout);}
  layout.field_ids.push(field.id);draftLayouts=draftLayouts.filter(item=>item.field_ids.length);
  field.mapping_type = rules.mapping_type;
  const types={char:'CHAR',integer:'INTEGER EXTERNAL',decimal:'DECIMAL EXTERNAL',float:'FLOAT EXTERNAL',date:`DATE \"${rules.date_format||'YYYYMMDD'}\"`,unknown:'Unresolved'};
  field.datatype_conversion=rules.conversion==='to_number'?'TO_NUMBER':rules.conversion==='to_date'?`TO_DATE(value, '${rules.date_format||'YYYYMMDD'}')`:rules.conversion==='trim'?'TRIM':types[rules.datatype]||field.datatype_conversion;
  field.padding_alignment = rules.padding.side === 'none' ? 'No padding' : `${rules.padding.side === 'right' ? 'Left aligned, right' : 'Right aligned, left'} ${rules.padding.char === ' ' ? 'space' : `“${rules.padding.char}”`} padding`;
  specDirty = true; $('#detailDialog').close(); render();
}

$$('[data-stage]').forEach(button => button.addEventListener('click', () => setStage(button.dataset.stage)));
$('#dismissNotice').onclick = () => $('#notice').classList.add('hidden');
$('#loginForm').onsubmit = event => { event.preventDefault(); withBusy(event.submitter,'Signing in…',async () => { await request('/api/session',{method:'POST',body:JSON.stringify({access_token:$('#accessToken').value})}); $('#accessToken').value=''; await initialize(); }); };
$('#newProjectButton').onclick = async () => {
  if ((specDirty || valueEdits.size || batchSpecDirty) && !await confirmAction('Leave unsaved changes and start a new project?')) return;
  clearTimeout(pollTimer); state=null; projectId=null; draftFields=[]; draftOptions={}; draftLayouts=[]; specDirty=false; valueEdits.clear(); batchState=null; batchSpecId=null; batchSpecDraft=null; batchSpecDirty=false; batchSpecEditing=false; batchDatId=null; batchRecords=[]; batchRecord=null; batchRecordDetail=null; batchOffset=0; batchTotal=0; batchFieldId=null; stage='upload';
  $('#projectName').value=''; $('#uploadForm').reset(); $('#batchProjectName').value=''; $('#batchUploadForm').reset(); render(); await listProjects(); $('#projectName').focus();
};
$('#refreshProject').onclick = () => withBusy($('#refreshProject'),'Refreshing…',async () => {
  if ((specDirty || valueEdits.size || batchSpecDirty) && !await confirmAction('Discard unsaved changes and reload the saved project?')) return;
  specDirty=false;valueEdits.clear();batchSpecDirty=false;await refreshProject();if(stage==='batch')await loadBatch();notify('Saved project reloaded.');
});
for (const [input,label] of [['specFile','specFileLabel'],['datFile','datFileLabel']]) $('#'+input).onchange = () => { $('#'+label).textContent=$('#'+input).files[0]?.name || 'No file selected'; };
$('#uploadForm').onsubmit = event => {
  event.preventDefault();
  withBusy($('#uploadButton'),'Uploading files…',async () => {
    if (state?.document) throw Error('Start a new project to upload another source set.');
    if (!projectId) { const created=await request('/api/projects',{method:'POST',body:JSON.stringify({name:$('#projectName').value.trim()})}); projectId=created.project.id; state=created; }
    const files=new FormData();files.set('spec',$('#specFile').files[0]);files.set('dat',$('#datFile').files[0]);files.set('version',state.project.version);files.set('encoding',$('#encoding').value);
    $('#uploadProgress').classList.remove('hidden');$('#uploadProgress').removeAttribute('value');
    try { applyState(await request(projectPath('/files'),{method:'POST',body:files}));stage='spec';render();await listProjects();notify('Files uploaded. Interpret the specification, then review and approve its fields.'); }
    finally { $('#uploadProgress').classList.add('hidden'); }
  });
};
for (const [input,label] of [['batchSpecFiles','batchSpecFileLabel'],['batchDatFiles','batchDatFileLabel']]) $('#'+input).onchange = () => { const files=$('#'+input).files; $('#'+label).textContent = files.length ? [...files].map(file=>file.name).join(' · ') : 'No files selected'; };
$('#batchUploadForm').onsubmit = event => {
  event.preventDefault();
  withBusy($('#batchUploadButton'),'Uploading…',async () => {
    const specs=[...$('#batchSpecFiles').files], dats=[...$('#batchDatFiles').files];
    if (!specs.length && !dats.length) throw Error('Choose at least one specification or DAT file.');
    if (!projectId) { const created=await request('/api/projects',{method:'POST',body:JSON.stringify({name:$('#batchProjectName').value.trim()})}); state=created.state||created; projectId=state.project.id; }
    if (!batchState) await loadBatch();
    const files=new FormData();
    specs.forEach(file=>files.append('specs',file));dats.forEach(file=>files.append('dats',file));
    files.set('version',batchState.project.version);files.set('encoding',$('#batchEncoding').value);
    acceptBatch(await request(batchPath('/files'),{method:'POST',body:files}));
    $('#batchSpecFiles').value='';$('#batchDatFiles').value='';$('#batchSpecFileLabel').textContent='No specifications selected';$('#batchDatFileLabel').textContent='No DAT files selected';
    await listProjects();notify('Files uploaded. Review and approve specifications, then assign their layouts to DAT files.');
  });
};
$('#batchSpecSelect').onchange = event => {if(batchSpecDirty){notify('Save the current specification draft before selecting another.',true);event.target.value=batchSpecId;return;}batchSpecId=event.target.value;batchSpecEditing=false;batchSpecDraft=null;renderBatch();};
$('#batchEditSpec').onclick = async () => {if(!await confirmAction('Revise this approved specification? Saving a new draft requires review and approval again, and previous exports must be regenerated.'))return;batchSpecEditing=true;renderBatch();};
$('#batchSaveSpec').onclick = () => withBusy($('#batchSaveSpec'),'Saving…',async () => {const spec=chosenBatchSpec();if(!spec||!batchSpecDraft)return;await batchAction(`/specs/${encodeURIComponent(spec.id)}`,{fields:batchSpecDraft.fields,layouts:batchSpecDraft.layouts,options:batchSpecDraft.options},'PUT');batchSpecDirty=false;batchSpecEditing=false;await loadBatch();notify('Specification draft saved. Approval and previous generated outputs are invalidated.');});
$('#batchApproveSpec').onclick = () => withBusy($('#batchApproveSpec'),'Approving…',async () => {const spec=chosenBatchSpec();if(!spec)return;if(!await confirmAction(`Approve ${spec.name} version ${spec.version} and its routing rules for this batch?`))return;await batchAction(`/specs/${encodeURIComponent(spec.id)}/approve`);notify('Specification approved. Assign its layouts to DAT files before analysis.');});
$('#batchSaveAssociations').onclick = () => withBusy($('#batchSaveAssociations'),'Saving…',async () => {const groups=new Map();$$('[data-batch-layout-id]:checked').forEach(input=>{const key=`${input.dataset.batchDatId}:${input.dataset.batchSpecId}`;if(!groups.has(key))groups.set(key,{dat_id:input.dataset.batchDatId,spec_id:input.dataset.batchSpecId,layout_ids:[]});groups.get(key).layout_ids.push(input.dataset.batchLayoutId);});await batchAction('/associations',{associations:[...groups.values()]},'PUT');notify('Explicit DAT-to-specification associations saved.');});
$('#batchProcess').onclick = () => withBusy($('#batchProcess'),'Analyzing…',async () => {await batchAction('/process');await loadBatchRecords();notify('Batch analyzed. Review every unresolved record before export.');});
$('#batchAi').onclick = () => withBusy($('#batchAi'),'Auditing…',async () => {try{await batchAction('/ai');notify('AI audit completed. Deterministic checks still control export.');}catch(error){await loadBatch();throw error;}});
$('#batchGenerate').onclick = () => withBusy($('#batchGenerate'),'Validating…',async () => {await batchAction('/generate');notify('Export eligibility recalculated. Download only files marked eligible.');});
async function batchDownload(path) {const response=await fetch(batchPath(path),{headers:{'X-Studio-Request':'1'}});if(!response.ok){const body=await response.json().catch(()=>null);throw Error(errorMessage(body?.detail||body));}const blob=await response.blob();const name=response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1]||'batch-export';const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
$('#batchDownloadArchive').onclick = () => withBusy($('#batchDownloadArchive'),'Preparing archive…',()=>batchDownload('/archive'));
$('#closeBatchRules').onclick=$('#cancelBatchRules').onclick=()=>$('#batchRuleDialog').close();
$('#saveBatchRules').onclick=saveBatchRules;
$('#batchPreviousRecords').onclick = () => withBusy($('#batchPreviousRecords'),'Loading…',async()=>{batchOffset=Math.max(0,batchOffset-100);await loadBatchRecords();});
$('#batchNextRecords').onclick = () => withBusy($('#batchNextRecords'),'Loading…',async()=>{batchOffset+=100;await loadBatchRecords();});
$('#saveSpecButton').onclick = () => withBusy($('#saveSpecButton'),'Saving…',saveSpec);
$('#analyzeSpecButton').onclick = () => withBusy($('#analyzeSpecButton'),'Starting interpretation…',async () => { await saveSpec(); await action('/analyze-spec');notify('Specification interpretation submitted. You can follow its progress above.'); });
$('#approveSpecButton').onclick = () => withBusy($('#approveSpecButton'),'Approving…',async () => {
  if (state.spec.approved && !specDirty) {setStage('dat');return;}
  if (!await confirmAction('Approve this specification as the rules for reading and formatting the DAT? Review examples, positions and uncertain rules first.')) return;
  await saveSpec();await action('/approve');stage='dat';render();notify('Specification approved. Analyze the DAT to review actual field values.');
});
$('#editSpecButton').onclick = () => setStage('spec');
$('#analyzeDatButton').onclick = () => withBusy($('#analyzeDatButton'),'Starting analysis…',async () => {await saveValues();await action('/analyze-dat');notify('DAT analysis submitted. Every record will also receive local validation.');});
$('#saveValuesButton').onclick = () => withBusy($('#saveValuesButton'),'Saving values…',saveValues);
for (const actionName of ['undo','redo']) $('#'+actionName+'Button').onclick = () => withBusy($('#'+actionName+'Button'),actionName==='undo'?'Undoing…':'Redoing…',async () => {
  if (specDirty || valueEdits.size) {if(!await confirmAction('Discard unsaved changes before restoring saved history?'))return;specDirty=false;valueEdits.clear();}
  await action('/history/'+actionName);notify(actionName==='undo'?'Previous saved work restored.':'Saved change restored.');
});
$('#fieldSearch').oninput = renderTable;$('#issuesOnly').onchange = renderTable;
$('#applyBulkMandatory').onclick = async () => {
  const value = $('#bulkMandatory').value;
  if (!value) { notify('Choose Yes, No, or Unknown first.', true); return; }
  const search = $('#fieldSearch').value.toLowerCase();
  const selected = draftFields.filter(field => !search || `${field.field_name} ${field.category_functional_group}`.toLowerCase().includes(search));
  if (!selected.length) { notify('No fields match the current filter.', true); return; }
  if (!await confirmAction(`Set mandatory status to ${value} for ${selected.length} filtered fields? This changes the specification draft.`)) return;
  for (const field of selected) { field.mandatory = value; field.rules.required = value === 'YES' ? true : value === 'NO' ? false : null; }
  specDirty = true; render(); notify(`Updated ${selected.length} draft fields. Save and review the specification before approval.`);
};
$('#ackUnknownMandatory').onchange = event => {
  draftOptions.acknowledge_unknown_mandatory = event.target.checked;
  specDirty = true; renderReviewStatus(); renderButtons();
};
$('#recordNumber').onchange = () => withBusy(null,'',() => changeRecord(Number($('#recordNumber').value)));
$('#previousRecord').onclick = () => withBusy(null,'',() => changeRecord(state.review.record.number-1));
$('#nextRecord').onclick = () => withBusy(null,'',() => changeRecord(state.review.record.number+1));
$('#nextIssue').onclick = () => withBusy($('#nextIssue'),'Finding issue…',nextIssue);
$('#generateButton').onclick = () => withBusy($('#generateButton'),'Validating current DAT…',async () => {await saveValues();await action('/generate');notify('The output was formatted and validated. Review the result before downloading.');});
$('#downloadOriginal').onclick = () => {requireProject();window.location.assign(projectPath('/download?original=true'));};
$('#downloadSpec').onclick = () => {requireProject();window.location.assign(projectPath('/approved-spec'));};
$('#downloadReport').onclick = () => {requireProject();window.location.assign(projectPath('/report'));};
$('#downloadAudit').onclick = () => {requireProject();window.location.assign(projectPath('/audit'));};
$('#downloadDat').onclick = () => {requireProject();window.location.assign(projectPath('/download'));};
$('#closeDetail').onclick = $('#cancelDetail').onclick = () => $('#detailDialog').close();$('#saveRulesButton').onclick=applyRuleEdits;

document.addEventListener('click', event => {
  const batchButton=event.target.closest('button');
  if(batchButton?.dataset.batchSpec){if(batchSpecDirty){notify('Save the current specification draft before selecting another.',true);return;}batchSpecId=batchButton.dataset.batchSpec;batchSpecDraft=null;batchSpecEditing=false;renderBatch();return;}
  if(batchButton?.dataset.batchDat){batchDatId=batchButton.dataset.batchDat;batchRecords=[];batchRecord=null;batchRecordDetail=null;batchOffset=0;batchTotal=0;renderBatch();withBusy(null,'',loadBatchRecords);return;}
  if(batchButton?.dataset.batchRecord!==undefined){withBusy(batchButton,'Loading…',()=>selectBatchRecord(Number(batchButton.dataset.batchRecord)));return;}
  if(batchButton?.dataset.batchFieldEvidence){batchFieldId=batchButton.dataset.batchFieldEvidence;renderBatchRecords();return;}
  if(batchButton?.dataset.batchDownload){withBusy(batchButton,'Downloading…',()=>batchDownload(`/download/${encodeURIComponent(batchButton.dataset.batchDownload)}`));return;}
  if(batchButton?.dataset.batchOriginal){withBusy(batchButton,'Downloading…',()=>batchDownload(`/download/${encodeURIComponent(batchButton.dataset.batchOriginal)}?original=true`));return;}
  if(batchButton?.dataset.batchSpecSource){withBusy(batchButton,'Downloading…',()=>batchDownload(`/specs/${encodeURIComponent(batchButton.dataset.batchSpecSource)}/source`));return;}
  if(batchButton?.id==='batchSaveAiDecision'){withBusy(batchButton,'Saving…',async()=>{const record=batchRecordDetail,reason=$('#batchAiReason')?.value.trim();if(!record||!reason||reason.length<3)throw Error('Explain the AI review decision in at least three characters.');const evidence=record.ai_review?.evidence_id||record.evidence_id;if(!evidence)throw Error('AI evidence identifier is unavailable; rerun the audit.');await batchAction(`/records/${encodeURIComponent(batchDatId)}/${record.number}/ai-decision`,{evidence_id:evidence,decision:$('#batchAiDecision').value,reason});await selectBatchRecord(batchRecord);notify('AI review decision saved. Export still depends on deterministic validation.');});return;}
  if(batchButton?.dataset.batchAddCondition!==undefined){const layout=batchSpecDraft?.layouts?.[Number(batchButton.dataset.batchAddCondition)];if(!layout)return;layout.when=layout.when||[];layout.when.push({start:1,end:1,equals:''});batchSpecDirty=true;renderBatch();return;}
  if(batchButton?.dataset.batchRuleIndex!==undefined){openBatchRules(Number(batchButton.dataset.batchRuleIndex));return;}
  const row = event.target.closest('[data-field-row]');
  if (row) { selectedFieldId = row.dataset.fieldRow; renderFieldContext(); }
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.openProject)withBusy(button,'Opening…',()=>openProject(button.dataset.openProject));
  if(button.dataset.addCondition!==undefined){draftLayouts[Number(button.dataset.addCondition)].when.push({start:1,end:2,equals:''});specDirty=true;renderFileRules();renderReviewStatus();renderButtons();}
  if(button.dataset.removeCondition){const [layout,index]=button.dataset.removeCondition.split(':').map(Number);draftLayouts[layout].when.splice(index,1);specDirty=true;renderFileRules();renderReviewStatus();renderButtons();}
  if(button.dataset.details)openDetails(button.dataset.details);
  if(button.dataset.focusValue)$$('[data-value-field]').find(input=>input.dataset.valueField===button.dataset.focusValue)?.focus();
  if(button.dataset.confirmBoundary)withBusy(button,'Confirming…',async()=>{if(!await confirmAction('Confirm that you have reviewed this record’s source field boundaries? This does not approve other failed checks.'))return;await saveValues();await action('/confirm',{record:Number(button.dataset.confirmBoundary)});notify('Source interpretation confirmed. Review any remaining failed checks.');});
  if(button.dataset.rejectAi)withBusy(button,'Saving decision…',async()=>{const reason=window.prompt('Why do you reject this AI advice? The deterministic checks will still apply.');if(!reason||reason.trim().length<3)return;await action('/ai-decision',{record:Number(button.dataset.aiRecord),evidence_id:button.dataset.rejectAi,decision:'reject',reason:reason.trim()});notify('AI decision saved. Deterministic checks still control export.');});
  if(button.dataset.stage && !button.classList.contains('nav-item') && !button.classList.contains('stage-step'))setStage(button.dataset.stage);
});
document.addEventListener('input', event => {
  const input=event.target;
  if(input.dataset.batchField!==undefined){const field=batchSpecDraft?.fields?.[Number(input.dataset.batchField)];if(!field)return;field[input.dataset.batchColumn]=input.value;if(input.dataset.batchColumn==='mandatory'&&field.rules)field.rules.required=input.value==='YES'?true:input.value==='NO'?false:null;batchSpecDirty=true;input.classList.add('changed');$('#batchSpecStatus').textContent='Unsaved specification changes. Save the draft before approval.';$('#batchSaveSpec').disabled=false;return;}
  if(input.dataset.batchLayout!==undefined){const condition=batchSpecDraft?.layouts?.[Number(input.dataset.batchLayout)]?.when?.[Number(input.dataset.batchCondition)];if(!condition)return;const key=input.dataset.batchKey;condition[key]=key==='equals'?input.value:Number(input.value);batchSpecDirty=true;$('#batchSaveSpec').disabled=false;return;}
  if(input.matches('[data-spec-field]')){
    const field=draftFields.find(item=>item.id===input.dataset.specField);if(!field)return;
    field[input.dataset.column]=input.value;specDirty=true;input.classList.add('changed');
    if(input.dataset.column==='mandatory') field.rules.required = input.value==='YES' ? true : input.value==='NO' ? false : null;
    $('#saveStatus').textContent='Unsaved changes';$('#saveStatus').className='status-tag dirty';$('#pendingEdits').textContent='Specification draft changed';renderButtons();
  }
  if(input.matches('[data-value-field]')){
    const result=reviewField(input.dataset.valueField);if(!result)return;
    const record=state.review.record.number,key=`${record}:${result.id}`;
    if(input.value===(result.value??''))valueEdits.delete(key);else valueEdits.set(key,{record,field_id:result.id,value:input.value});
    input.classList.toggle('changed',valueEdits.has(key));$('#saveStatus').textContent=valueEdits.size?'Unsaved changes':`Saved · version ${state.project.version}`;$('#saveStatus').className=`status-tag ${valueEdits.size?'dirty':'saved'}`;$('#pendingEdits').textContent=`${valueEdits.size} value change(s) to save`;renderButtons();renderGeneration();
  }
});
document.addEventListener('change', async event => {
  const input=event.target;
  if(input.id==='batchAckUnknown'&&batchSpecDraft){batchSpecDraft.options=batchSpecDraft.options||{};batchSpecDraft.options.acknowledge_unknown_mandatory=input.checked;batchSpecDirty=true;$('#batchSaveSpec').disabled=false;return;}
  if(input.matches('[data-spec-field][data-column="mandatory"]')){input.dispatchEvent(new Event('input',{bubbles:true}));renderReviewStatus();return;}
  if(input.dataset.option){const key=input.dataset.option;if(key==='unknown_records'&&input.value==='preserve'&&!await confirmAction('Preserve every unrecognized record exactly as uploaded? These records will not be validated against field rules. This policy takes effect only after specification approval.')){input.value=draftOptions[key]||'block';return;}if(key==='record_length'){if(input.value)draftOptions[key]=Number(input.value);else delete draftOptions[key];}else draftOptions[key]=input.value;specDirty=true;renderReviewStatus();renderButtons();$('#saveStatus').textContent='Unsaved changes';$('#saveStatus').className='status-tag dirty';return;}
  if(input.dataset.layoutIndex!==undefined){const layout=draftLayouts[Number(input.dataset.layoutIndex)];if(input.dataset.conditionIndex!==undefined){const key=input.dataset.conditionKey;layout.when[Number(input.dataset.conditionIndex)][key]=key==='equals'?input.value:Number(input.value);}else{const key=input.dataset.layoutKey;layout[key]=key==='record_length'?Number(input.value):input.value;}specDirty=true;renderReviewStatus();renderButtons();$('#saveStatus').textContent='Unsaved changes';$('#saveStatus').className='status-tag dirty';return;}
  if(!input.matches('[data-value-field]'))return;
  const normalized=input.value.replace(/^ +| +$/g,'');if(normalized!==input.value){input.value=normalized;input.dispatchEvent(new Event('input',{bubbles:true}));notify('Surrounding spaces removed. The approved specification applies field padding.');}
});
document.addEventListener('keydown', event => {
  const input=event.target;
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();(stage==='spec'?$('#saveSpecButton'):$('#saveValuesButton')).click();return;}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'&&!input.matches('input,textarea')){event.preventDefault();$('#'+(event.shiftKey?'redoButton':'undoButton')).click();return;}
  if(!input.matches('[data-value-field],[data-spec-field]'))return;
  if(event.key==='Escape'){input.blur();return;}
  if(!['Enter','ArrowUp','ArrowDown'].includes(event.key)||event.key==='Enter'&&event.shiftKey)return;
  if(input.matches('[data-spec-field]')&&event.key!=='Enter')return;
  event.preventDefault();const inputs=$$(input.matches('[data-value-field]')?'[data-value-field]':'[data-spec-field]');const index=inputs.indexOf(input);const next=inputs[index+(event.key==='ArrowUp'?-1:1)];next?.focus();
});
window.addEventListener('beforeunload',event=>{if(specDirty||valueEdits.size||batchSpecDirty){event.preventDefault();event.returnValue='';}});
async function initialize(){
  const session=await request('/api/session');
  if(!session.authenticated){if(session.login_required){$('#loginPanel').classList.remove('hidden');$('#application').classList.add('hidden');return;}await request('/api/session',{method:'POST',body:'{}'});}
  $('#loginPanel').classList.add('hidden');$('#application').classList.remove('hidden');render();await listProjects();
  try {const ai=await request('/api/ai/status');$('#providerSummary').textContent=`Bedrock · ${ai.ai_available===false?'unavailable':ai.model_id||'not configured'}`;}catch{$('#providerSummary').textContent='AI connection not verified';}
}
initialize().catch(error=>notify(error.message,true));
