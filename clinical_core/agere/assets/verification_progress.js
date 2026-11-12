/* Read-only display of a saved attempt. No inference, retry or work mutation. */
function verificationProgressHTML(p) {
 if(!p || p.schema!=='saved-verification-progress/1' || p.status!=='interrupted')return '';
 const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fields=p.recorded_fields,counts=fields?[
  [fields.supported,'Fields with recorded support'],
  [fields.unresolved,'Fields still undecided'],
  [fields.faults,'Fields needing repair'],
  [fields.unchecked,'Fields not checked']]:[];
 const dependencies=p.service_dependencies||[], limited=dependencies.some(d=>d.kind==='rate_limit');
 const reason=limited?'A model service reported an account limit. Recovery and reset time have not been verified.':'The review stopped before all checks finished. Recovery has not been verified.';
 const ids=Array.isArray(p.unchecked_packet_ids)?p.unchecked_packet_ids:null;
 return '<section class="verification-progress" aria-label="Saved verification progress">'+
  '<div class="verification-progress-heading"><span class="verification-progress-mark" aria-hidden="true">Ⅱ</span><div><span class="verification-progress-kicker">WORK PRESERVED</span><h3>Review interrupted. Your progress is saved.</h3><p>'+escape(reason)+'</p></div></div>'+
  '<div class="verification-progress-grid">'+[[p.saved_execution_steps,'Saved execution steps'],...counts].map(([n,label])=>'<div><strong>'+escape(Number.isInteger(n)?n:'Unknown')+'</strong><span>'+escape(label)+'</span></div>').join('')+'</div>'+
  '<p class="verification-progress-boundary">These are records from the latest review attempt, not verification of the whole answer. A service interruption does not establish missing clinical evidence or patient eligibility.</p>'+
  '<details><summary>What remains &amp; service details</summary><p>'+(ids?escape(ids.length)+' source packets were not started.':'The number of unstarted source packets was not recorded.')+' Other checks may still be unfinished. No automatic retry is performed here.</p>'+
  (fields?'<p>'+escape(fields.native_supported)+' of the recorded supported fields used native provenance. '+escape(p.shared_program_reviews)+' shared program reviews were saved.</p>':'<p>Field-level completion counts were not recorded for this attempt.</p>')+
  '<ul>'+dependencies.map(d=>'<li>'+escape(d.error||'Interruption without a recorded diagnosis.')+'</li>').join('')+'</ul>'+
  (p.cost_known===false?'<p>Total review cost is unknown.'+(typeof p.known_cost_usd==='number'?' Recorded API-equivalent subtotal: $'+escape(p.known_cost_usd.toFixed(4))+'.':'')+'</p>':'')+
  '<p>Continue from this saved investigation after its dependency is resolved. Staff review notes do not complete machine verification or authorize care.</p></details></section>';
}
