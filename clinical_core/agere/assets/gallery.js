/* Creation library. Metadata is cheap; the original page loads only on open. */
let creationLibrary={items:[],loaded:false,flight:null,filter:'saved',page:0};
let creationPreviewRequest=0,creationReturnUrl=null;
const creationKinds={report:'Report',brief:'Brief',explorer:'Data explorer',page:'Interactive page'};
function creationLabel(item){return item.generation==='saved_report'?'Saved '+(creationKinds[item.kind]||'report').toLowerCase():creationKinds[item.kind]||'Saved page'}
function creationCover(item){
 return '<div class="creation-cover cover-'+esc(item.kind)+'" aria-hidden="true"><div class="cover-paper"><span class="cover-wordmark">✳ WOLLSTONECRAFT</span><strong>'+esc(item.title)+'</strong><div class="cover-rule"></div><div class="cover-lines"><i></i><i></i><i></i></div><div class="cover-panels"><i></i><i></i></div><span class="cover-caption">'+esc(creationKinds[item.kind]||'Creation')+'</span></div><span class="cover-open">Preview ↗</span></div>';
}
async function loadGallery(force=false){
 if(creationLibrary.flight)return creationLibrary.flight;
 if(creationLibrary.loaded&&!force){renderGallery();return creationLibrary}
 creationLibrary.flight=fetchJSON('/api/stack/deliverable-gallery').then(d=>{creationLibrary={...creationLibrary,...d,loaded:true};renderGallery();return creationLibrary}).catch(e=>{
  const host=$('#gallery-grid');if(host)host.innerHTML='<div class="library-empty"><h2>The gallery couldn’t load.</h2><p>'+esc(e.message)+'</p><button class="btn" data-gallery-reload>Try again</button></div>';throw e;
 }).finally(()=>{creationLibrary.flight=null});return creationLibrary.flight;
}
function galleryRows(){
 const term=($('#gallery-search')?.value||'').toLowerCase(),filter=creationLibrary.filter;
 return creationLibrary.items.filter(x=>(filter==='failed'?x.status==='failed':x.status==='saved'&&(filter==='saved'||filter==='page'&&x.kind==='page'||filter==='reports'&&x.kind!=='page'))&&(!term||[x.title,x.question,x.request,creationLabel(x)].join(' ').toLowerCase().includes(term)));
}
function renderGallery(){
 const host=$('#gallery-grid');if(!host)return;
 const rows=galleryRows(),size=12;creationLibrary.page=Math.max(0,Math.min(creationLibrary.page,Math.ceil(rows.length/size)-1));const start=creationLibrary.page*size;
 $('#gallery-caption').textContent=rows.length+' '+(creationLibrary.filter==='failed'?'unsaved '+(rows.length===1?'attempt':'attempts'):(rows.length===1?'creation':'creations'));
 document.querySelectorAll('[data-gallery-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.galleryFilter===creationLibrary.filter)));
 host.innerHTML=rows.slice(start,start+size).map(item=>'<article class="creation-card"><button class="creation-preview" data-gallery-open="'+esc(item.id)+'" data-gallery-context="'+esc(item.context_id)+'" aria-label="'+esc((item.status==='saved'?'Preview ':'Inspect attempt: ')+item.title)+'">'+creationCover(item)+'</button><div class="creation-meta"><span>'+esc(creationLabel(item))+'</span><span>'+esc(shortDate(item.created_at))+'</span></div><h2><button data-gallery-open="'+esc(item.id)+'" data-gallery-context="'+esc(item.context_id)+'">'+esc(item.title)+'</button></h2><p class="creation-origin">'+esc(item.question||item.request)+'</p><div class="creation-card-foot"><span class="creation-state '+(item.status==='saved'?'':'failed')+'">'+(item.status==='saved'?'Saved version':'Not created')+'</span><a href="'+esc(item.answer_url)+'">Source answer ↗</a></div></article>').join('')||'<div class="library-empty"><span aria-hidden="true">✳</span><h2>'+(creationLibrary.filter==='failed'?'No failed attempts here.':($('#gallery-search').value?'No matching creations.':'Make something worth coming back to.'))+'</h2><p>'+(creationLibrary.filter==='failed'?'Completed creations stay in the main gallery.':'Ask for a report, brief or interactive page. Saved outputs appear here automatically.')+'</p><button class="btn primary" data-new-creation>Ask your data ↗</button></div>';
 host.querySelectorAll('.creation-card').forEach((card,i)=>{const item=rows[start+i];if(item?.experience?.kind==='ckd'){const a=document.createElement('a');a.href=item.experience.url;a.className='creation-program-link';a.textContent='Open program workspace ↗';card.append(a)}});
 const program=creationLibrary.items.find(x=>x.status==='saved'&&x.experience?.kind==='ckd'),programHost=$('#home-programs');
 if(programHost)programHost.innerHTML=program?'<a class="program-entry" href="'+esc(program.experience.url)+'"><span class="program-entry-icon" aria-hidden="true">'+uiIcon('activity')+'</span><div><small>EXAMPLE PROGRAM WORKSPACE</small><strong>Kidney care, connected.</strong><p>Open the population, explore each patient, and trace the evidence.</p><span class="program-entry-action">Open CKD command center <b aria-hidden="true">↗</b></span></div></a>':'';
 $('#gallery-pagination').innerHTML=rows.length>size?'<span>'+(start+1)+'–'+Math.min(start+size,rows.length)+' of '+rows.length+'</span><div><button class="btn small" data-gallery-page="-1"'+(start===0?' disabled':'')+'>Previous</button><button class="btn small" data-gallery-page="1"'+(start+size>=rows.length?' disabled':'')+'>Next</button></div>':'';
 const home=$('#home-creations');if(home)home.innerHTML=creationLibrary.items.filter(x=>x.status==='saved').slice(0,3).map(item=>'<button class="home-creation" data-gallery-open="'+esc(item.id)+'" data-gallery-context="'+esc(item.context_id)+'">'+creationCover(item)+'<strong>'+esc(item.title)+'</strong><small>'+esc(creationLabel(item))+'</small></button>').join('')||'<p class="hint">Your saved reports and interactive pages will appear here.</p>';
}
function sandboxedCreation(html){
 const policy='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; font-src data:; connect-src \'none\'; form-action \'none\'; base-uri \'none\'; object-src \'none\'">';
 const chrome='<style>.wc-shell-nav,.wc-shell-header{display:none!important}.wc-shell-page{margin-left:0!important}body{margin:0!important;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif!important}.wrap{max-width:1300px!important}header h1{font:500 28px/1.3 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif!important;letter-spacing:-.8px!important}header #question{font-size:12px!important;line-height:1.6!important}.kpis{display:flex!important;flex-wrap:wrap!important;gap:0!important;border-bottom:1px solid #e4deeb!important;padding-bottom:18px!important}.kpis>*{flex:1!important;min-width:135px!important;box-shadow:none!important;border:0!important;border-right:1px solid #e4deeb!important;background:none!important;border-radius:0!important;padding:5px 18px!important}.kpis strong{font:500 25px/1.3 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif!important}.notice{background:none!important;border:0!important;border-left:2px solid #b9a3d2!important;font-size:11px!important}.toolbar{font-size:10px!important}#patient-workspace h2{font-size:32px!important}</style>';
 return /<head\b[^>]*>/i.test(html)?html.replace(/<head\b[^>]*>/i,m=>m+policy+chrome):policy+chrome+html;
}
async function openCreation(context,id){
 if(!/^ANS-[0-9a-f]{24}$/.test(context)||!/^DLV-[0-9A-F]{12}$/.test(id))return;
 const dialog=$('#creation-dialog'),request=++creationPreviewRequest,item=creationLibrary.items.find(x=>x.id===id&&x.context_id===context);
 $('#creation-title').textContent=item?.title||'Saved creation';$('#creation-status').textContent='Opening saved version…';$('#creation-body').innerHTML='<div class="creation-loading" role="status">Loading your creation…</div>';$('#creation-source').href='/ask?context='+context;
 if(!dialog.open){creationReturnUrl=new URL(location.href);creationReturnUrl.searchParams.delete('artifact');lastDialogFocus=document.activeElement;dialog.showModal()}
 const url=new URL(location.href);url.searchParams.set('context',context);url.searchParams.set('artifact',id);url.hash='gallery';history.replaceState(null,'',url);
 try{
  const d=await fetchJSON('/api/stack/deliverable-preview?context='+context+'&id='+id);if(request!==creationPreviewRequest||!dialog.open)return;
  if(d.status!=='saved'||!d.html){$('#creation-status').textContent='This attempt did not create a page';$('#creation-body').innerHTML='<div class="creation-problem"><h3>The design wasn’t saved.</h3><p>Generation stopped before a page was saved. Your source answer is still available.</p><details><summary>Recorded failure</summary><p>'+esc(d.failure||'Generation did not finish.')+'</p></details></div>';return}
  $('#creation-status').textContent=d.historical?'Historical version · source or definitions need review. Viewing does not enable new work.':'Saved version · read-only preview';
  const frame=document.createElement('iframe');frame.title=item?.title||'Saved creation';frame.setAttribute('sandbox','allow-scripts allow-downloads');frame.srcdoc=sandboxedCreation(d.html);$('#creation-body').replaceChildren(frame);
 }catch(e){if(request!==creationPreviewRequest||!dialog.open)return;$('#creation-status').textContent='This creation needs attention';$('#creation-body').innerHTML='<div class="creation-problem"><h3>This saved page couldn’t open.</h3><p>'+esc(e.message)+'</p><a class="btn" href="/ask?context='+context+'">Open its source answer ↗</a></div>'}
}
function requestedOutputs(t){
 const steps=t.data?.workflow||[],context=t.data?.action_context?.id;if(!steps.length||!context)return '';
 return '<section class="answer-creations" data-answer-creations><div class="section-row"><h3>Created from this answer</h3><button class="text-button" data-page="gallery">View gallery ↗</button></div>'+steps.map(s=>'<div class="output-row"><span class="output-symbol" aria-hidden="true">'+(s.kind==='deliverable'?'▤':'↗')+'</span><div><strong>'+esc(s.title)+'</strong><small>'+esc(s.kind==='deliverable'?(s.generation==='saved_report'?'Saved report':s.status==='ready'?'Saved page':fieldLabel(s.status)):fieldLabel(s.kind)+' · '+fieldLabel(s.status))+'</small></div>'+(s.deliverable_id&&s.status==='ready'?'<button class="btn small" data-gallery-open="'+esc(s.deliverable_id)+'" data-gallery-context="'+esc(context)+'">Open ↗</button>':'<span class="output-status">'+esc(s.status==='ready'?'Prepared':fieldLabel(s.status||'Not created'))+'</span>')+(/\bckd\b/i.test(s.title+' '+(s.quote||''))&&/program|nephrolog/i.test(s.title+' '+(s.quote||''))?'<a class="btn small" href="/ask?app=ckd&context='+encodeURIComponent(context)+'#program">Program workspace ↗</a>':'')+'</div>').join('')+'</section>';
}
function setupGallery(){
 document.addEventListener('click',e=>{const b=e.target.closest('[data-gallery-open],[data-gallery-filter],[data-gallery-page],[data-gallery-reload],[data-close-creation],[data-new-creation]');if(!b)return;
  if(b.hasAttribute('data-new-creation')){newConversation();showPage('explore',true);$('#q').focus()}
  if(b.dataset.galleryOpen)openCreation(b.dataset.galleryContext,b.dataset.galleryOpen);
  if(b.dataset.galleryFilter){creationLibrary.filter=b.dataset.galleryFilter;creationLibrary.page=0;renderGallery()}
  if(b.dataset.galleryPage){creationLibrary.page+=Number(b.dataset.galleryPage);renderGallery();$('#page-gallery').scrollIntoView({block:'start'})}
  if(b.hasAttribute('data-gallery-reload'))loadGallery(true).catch(()=>{});
  if(b.hasAttribute('data-close-creation'))$('#creation-dialog').close();
 });
 $('#gallery-search').addEventListener('input',()=>{creationLibrary.page=0;renderGallery()});
 $('#creation-dialog').addEventListener('close',()=>{creationPreviewRequest++;$('#creation-body').replaceChildren();lastDialogFocus?.focus();const url=creationReturnUrl||new URL(location.href);url.searchParams.delete('artifact');history.replaceState(null,'',url);creationReturnUrl=null});
 loadGallery().catch(()=>{});
}
