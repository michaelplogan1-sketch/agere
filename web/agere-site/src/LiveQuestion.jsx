import React, {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, BookOpen, ArrowDown} from 'lucide-react';
import {SignalCube} from './EvidenceInstrument.jsx';
import './live-question.css';

const validContext = id => /^ANS-[0-9a-f]{24}$/.test(id || '');
const format = n => Number.isFinite(n) ? n.toLocaleString() : '—';
async function read(url, options) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw Error('The workspace could not return an answer. Your question is still here.'); }
  if (!response.ok || data.error) throw Error(data.error || `The workspace returned HTTP ${response.status}.`);
  return data;
}

export function LiveQuestion() {
  const [question, setQuestion] = useState(''), [result, setResult] = useState(null);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [elapsed, setElapsed] = useState(0);
  const [meta, setMeta] = useState(null), [connection, setConnection] = useState('Connecting to your workspace…');
  const [learning, setLearning] = useState(null);
  const submitting = useRef(false);
  const refreshLearning = () => read('/api/stack/learning').then(setLearning).catch(() => setLearning(null));
  useEffect(() => {
    let current = true;
    read('/api/stack/meta').then(data => { if (current) { setMeta(data.retrieval); setConnection('Connected · synthetic workspace'); } })
      .catch(() => { if (current) setConnection('Workspace unavailable. Retry by reloading this page.'); });
    refreshLearning();
    const key = new URLSearchParams(window.location.search).get('context');
    if (key && validContext(key)) {
      setBusy(true); setRestoring(true);
      read('/api/stack/action-context?context=' + encodeURIComponent(key)).then(data => {
        if (!current) return;
        const c = data.context;
        setQuestion(c.question || '');
        setResult({question:c.question, answer:c.answer, action_context:{id:c.id}, workflow:data.workflow,
          archived:true, stale:c.stale || c.invalidated});
      }).catch(e => { if (current) setError(e.message); }).finally(() => { if (current) {setBusy(false); setRestoring(false);} });
    } else if (key) setError('This saved answer link is not valid. You can ask a new question below.');
    return () => { current = false; };
  }, []);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const interval = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    const warn = e => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => { clearInterval(interval); window.removeEventListener('beforeunload', warn); };
  }, [busy]);
  async function submit(event) {
    event.preventDefault();
    if (busy || submitting.current || !question.trim()) return;
    submitting.current = true; setBusy(true); setElapsed(0); setError(''); setResult(null);
    const q = question.trim();
    const url = new URL(window.location.href); url.searchParams.delete('context');
    window.history.replaceState(null, '', url);
    try {
      const data = await read('/api/stack/compose', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({question:q, scope:'warehouse'})});
      setResult(data);
      if (validContext(data.action_context?.id)) {
        url.searchParams.set('context', data.action_context.id); url.hash = 'ask';
        window.history.replaceState(null, '', url);
      }
      refreshLearning();
    } catch(e) { setError(e.message); }
    finally { submitting.current = false; setBusy(false); }
  }
  const answer = result?.answer || {};
  const context = result?.action_context?.id;
  const steps = Array.isArray(result?.workflow) ? result.workflow : result?.workflow?.steps || [];
  return <section className="answer-explorer live-explorer" id="ask">
    <div className="answer-explorer-intro"><span className="mono">ASK / YOUR CONNECTED WORKSPACE</span>
      <h2>One question.<br/><em>A clearer picture.</em></h2>
      <p>Ask about your population, care access, provider performance, or financial risk. Explore the evidence, flag a correction, and carry the answer into your next step.</p>
      <div className="answer-route">{['Data','Intelligence','Knowledge','Wisdom'].map((x,i)=><div key={x}><span className="route-block" aria-hidden="true"/><span className="mono">0{i+1}</span><strong>{x}</strong>{i<3&&<ArrowDown size={14}/>}</div>)}</div>
      <div className="explorer-population"><strong>{format(meta?.patients)}</strong><span>synthetic patients in the active workspace<br/>{format(meta?.visible_records)} received records<br/><small>Snapshot: {meta?.snapshot?.as_of || 'connecting…'}</small></span></div>
      <p className="live-connection" role="status">{connection}</p>
      <div className="live-learning"><BookOpen size={17}/><div><strong>Keep learning from the work.</strong><p>Questions and corrections feed the learning review. Checked lessons apply only where their evidence and scope fit.</p>{learning && <p>{format(Object.values(learning.statuses || {}).reduce((a,b)=>a+b,0))} recorded outcomes · {format(learning.corrections)} corrections</p>}<a href="/learn">Open learning review <ArrowUpRight size={14}/></a></div></div>
    </div>
    <div className="question-console"><div className="question-console-head"><span><SignalCube/>Ask Agere</span><span className="mono">LIVE · SYNTHETIC DATA</span></div>
      <form className="live-form" onSubmit={submit}><label className="mono" htmlFor="live-question">WHAT WOULD YOU LIKE TO UNDERSTAND?</label>
        <textarea id="live-question" value={question} onChange={e=>setQuestion(e.target.value)} readOnly={busy} required rows={5} placeholder="Ask your question. Include the population, dates, and what you want to do next."/>
        <p className="live-scope">Each question starts from the full connected warehouse. Continue an answer’s saved population in the analysis workspace.</p>
        <div className="live-submit"><button className="primary-button" type="submit" disabled={busy || !question.trim()}>{busy?(restoring?'Opening saved answer…':'Working through your question…'):'Ask Agere'}<ArrowUpRight size={17}/></button><a href="/ask#saved">Saved analyses</a></div>
      </form>
      <div className="live-result" aria-live="polite" aria-busy={busy}>
        {busy && <div className="live-pending" role="status"><span className="live-pulse"/><strong>{restoring?'Opening your saved answer':'Following the evidence'}</strong><p>{restoring?'Loading the saved result and current work status.':'Interpreting your question and preparing any requested work. Keep this page open.'}</p>{!restoring && <span>{elapsed} seconds elapsed</span>}</div>}
        {error && <div className="live-error" role="alert"><h3>This question needs another look.</h3><p>{error}</p><p>Your question is preserved above. You can edit it and try again.</p></div>}
        {result && <article><span className="mono">{result.archived?'SAVED ANSWER':answer.status === 'answered'?'ANSWER':'REVIEW NEEDED'}{answer.status?' · '+answer.status.replaceAll('_',' '):''}</span>
          <h3>{answer.headline || 'Open the analysis to review the result.'}</h3>
          <p className="live-original">{result.question}</p>
          {result.stale && <p className="live-error">The source or interpretation has changed. Review the saved analysis and ask again before preparing new work.</p>}
          {answer.status && answer.status !== 'answered' && <p className="live-error">This result has unresolved requirements. Review the limitations before taking a next step.</p>}
          {(answer.warnings || []).length > 0 && <details><summary>Answer limitations</summary><ul>{answer.warnings.map((w,i)=><li key={i}>{String(w)}</li>)}</ul></details>}
          {steps.length > 0 && <ul className="live-workflow">{steps.map((s,i)=><li key={i}><strong>{s.title || s.kind}</strong><span>{s.status}{s.kind==='monitor'?' · manual checks':''}</span></li>)}</ul>}
          {validContext(context) && <div className="live-links"><a className="primary-button" href={'/ask?context='+encodeURIComponent(context)}>Inspect answer & evidence <ArrowUpRight size={16}/></a><a href={'/action-center?context='+encodeURIComponent(context)}>Open brief & follow-up work <ArrowUpRight size={16}/></a></div>}
        </article>}
        {!busy && !result && !error && <div className="live-empty"><span className="mono">YOUR QUESTION → EVIDENCE → NEXT STEP</span><p>Your answer will appear here. You can ask for a brief or a manual monitor in the same question.</p></div>}
      </div>
      <div className="question-console-foot"><span className="mono">SOURCE-LINKED · SAVED IN YOUR WORKSPACE</span><a href="/ask">Open full workspace <ArrowUpRight size={13}/></a></div>
    </div>
  </section>;
}
