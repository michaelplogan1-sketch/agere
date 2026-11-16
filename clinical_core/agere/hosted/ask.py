"""Hosted Ask: a signed-in user's question goes through the tiered core over the server's demonstration data.

    available()                      the tiered core is on and its semantic layer file exists on this server
    answer(ctx, org, uid, body, ip)  -> (status, payload); follow-ups use the previous question in the same conversation
    page()                           the Ask page

The demonstration semantic layer is synthetic and shared by every organization on the server; it is not tenant data.
Bottles, recipes, System One, the small model, a frontier-written recipe, then frontier takeover (bounded). The older
deep path is not part of the hosted service. Limits: AGERE_ASK_PER_HOUR per organization (default 120) and
AGERE_ASK_CONCURRENT at once on the server (default 6). Usage and an audit line are recorded for every question.
"""
from .auth import question_ref
import html as _html
import os
import secrets
import threading
import time
from pathlib import Path

PER_HOUR = int(os.environ.get('AGERE_ASK_PER_HOUR') or 120)
_slots = threading.BoundedSemaphore(int(os.environ.get('AGERE_ASK_CONCURRENT') or 6))
_lock = threading.Lock()
_previous = {}  # (org, user, conversation) -> last complete questions (up to 4, oldest first); memory only, bounded
_groups = {}    # (org, user, conversation) -> patient ids of the last list answer, for "those patients" follow-ups
_answers = {}   # (org, user, answer id) -> what answered that question, for a report that it is wrong (server-side, never sent by the client)
GROUP_WORDS = __import__('re').compile(r'\b(them|those|these|they|their)\b', __import__('re').I)
MAX_CONVERSATIONS = 5000
MAX_ROWS = 300


def available():
    from ..tiered import app, store
    return app.enabled() and Path(store.path()).is_file()


def status():
    from ..tiered import bottles, store
    if not available():
        return {'available': False}
    db = store.connect()
    try:
        patients = db.execute('SELECT COUNT(*) FROM patient').fetchone()[0]
        as_of = store.as_of(db)
    finally:
        db.close()
    try:
        saved = sum(1 for b in bottles.Bottles().full() if b['status'] == 'promoted')
    except Exception:  # noqa: BLE001 - the count is informational
        saved = None
    return {'available': True, 'data': 'synthetic demonstration data', 'patients': patients, 'as_of': as_of, 'saved_recipes': saved}


def _looks_like_follow_up(question):
    try:
        from ..clinical.conversation import is_followup
        return bool(is_followup(question))
    except Exception:  # noqa: BLE001 - the simple rule is enough when the app module cannot load
        low = question.lower().strip()
        return len(low.split()) <= 4 or low.startswith(('and ', 'what about', 'how about', 'same ', 'now '))


def _shape(result, question, standalone, note):
    a = result['answer']
    table = a.get('table') or []
    def one(ans):
        t = ans.get('table') or []
        return {'headline': ans.get('headline'), 'columns': list(t[0]) if t else [], 'rows': [list(r.values()) for r in t[:MAX_ROWS]], 'row_count': len(t),
                'facts': ans.get('facts') or {}, 'limitations': ans.get('limitations') or [], 'grounding': (ans.get('grounding') or [])[:60],
                'verification': ans.get('verification'), 'computation': (ans.get('table_binding') or {}).get('computation')}
    out = one(a)
    out.update({'answered': True, 'question': question, 'read_as': standalone if standalone != question else None, 'note': note,
                'sections': [{'title': s['title'], **one(s['answer'])} for s in a.get('sections') or []],
                'tier': result['tiered_core']['final_tier'], 'seconds': result['tiered_core']['seconds'],
                'usd': round(result['planner_receipt']['known_api_estimate_usd'], 5), 'model_calls': result['new_paid_api_calls']})
    return out


def answer(ctx, org, uid, body, ip=None):
    from ..tiered import app
    if not available():
        return 503, {'error': 'Ask is not set up on this server (no demonstration data).'}
    question = ' '.join(str(body.get('question') or '').split())
    if not 3 <= len(question) <= 2000:
        return 400, {'error': 'Type a question (up to 2,000 characters).'}
    if not ctx.auth.allow(f'ask:{org}', PER_HOUR, 3600):
        return 429, {'error': f'This organization has asked {PER_HOUR} questions in the last hour. Try again later.'}
    conversation = str(body.get('conversation_id') or '')[:64] or secrets.token_urlsafe(9)
    key = (org, str(uid), conversation)
    with _lock:
        previous, group = _previous.get(key), _groups.get(key)
    if not _slots.acquire(timeout=20):
        return 503, {'error': 'The server is busy. Try again in a moment.'}
    started = time.monotonic()
    app.EARLIER.set(tuple(previous or ()))  # set per request: nothing carries over from another conversation
    from ..tiered import corrections
    corrections.ORG.set(str(org))   # reports this organization made block methods for it alone
    try:
        result, standalone, note = None, question, None
        if group and GROUP_WORDS.search(question):
            result = app.try_in_cohort(None, question, {'patient_ids': group}, f'{conversation}:group')
            if result: note = f'Asked about only the {len(group)} patients in the previous list.'
        if result is None and previous:
            follow = _looks_like_follow_up(question)
            got = app.try_in_conversation(None, question, previous[-1], follow, earlier=previous)
            if got: result, standalone, note = got
            elif follow:
                return 200, {'answered': False, 'question': question, 'conversation_id': conversation,
                             'message': 'I could not safely work out what this follow-up refers to. Ask it as a complete question.'}
        if result is None:
            result = app.try_answer(None, question)
    except Exception as exc:  # noqa: BLE001 - a provider limit or outage is reported, never a traceback
        print('ask error', type(exc).__name__, str(exc)[:200], flush=True)
        return 503, {'error': 'A model provider is unavailable or at its limit. Try again shortly.'}
    finally:
        _slots.release()
    seconds = round(time.monotonic() - started, 2)
    if result is None:
        ctx.auth.record_usage(org, 'ask', question_ref(question)['question_fingerprint'], 0.0, seconds)
        ctx.auth.audit(org, uid, 'ask', {**question_ref(question), 'answered': False}, ip=ip)
        return 200, {'answered': False, 'question': question, 'conversation_id': conversation,
                     'message': 'No method produced an answer inside the time limit. Try a narrower question (one measure, one period).'}
    out = _shape(result, question, standalone, note)
    out['conversation_id'] = conversation
    with _lock:
        if len(_previous) >= MAX_CONVERSATIONS: _previous.pop(next(iter(_previous)))
        _previous[key] = [*(previous or []), standalone][-4:]
        ids = [r[out['columns'].index('patient_id')] for r in ((result['answer'].get('table') and [list(x.values()) for x in result['answer']['table']]) or [])] if 'patient_id' in out['columns'] else []
        if ids: _groups[key] = sorted(set(map(str, ids)))
        receipt = result.get('planner_receipt') or {}
        out['answer_id'] = secrets.token_urlsafe(9)
        _answers[(org, str(uid), out['answer_id'])] = {'question': standalone, 'tier': out['tier'], 'recipe': receipt.get('recipe') or {}, 'rows': out.get('rows') or [],
                         'computation': out.get('computation')}
        if len(_answers) > MAX_CONVERSATIONS: _answers.pop(next(iter(_answers)))
        if len(_groups) > MAX_CONVERSATIONS: _groups.pop(next(iter(_groups)))
    ctx.auth.record_usage(org, 'ask', question_ref(question)['question_fingerprint'], out['usd'], seconds)
    ctx.auth.audit(org, uid, 'ask', {**question_ref(question), 'answered': True, 'tier': out['tier'], 'usd': out['usd']}, ip=ip)
    return 200, out


TIER_LABEL = {'bottles': 'Saved recipe', 'recipes': 'Built-in recipe', 'slm': 'System One + small model', 'llm': 'Frontier-written recipe',
              'takeover': 'Frontier model took over'}

EXAMPLES = ['How many ED visits were there in 2029?', 'What was the no-show rate by clinic in 2029?',
            "Why isn't patient P-HF07 on all guideline heart failure therapy?",
            'How many radiology reports in 2029 mention a pulmonary nodule?',
            'Do patients with diabetes have more ED visits per patient in 2029 than patients without diabetes?']


def page():
    from . import pages
    body = f'''<header class="top" id="orgbar"></header>
<main class="section"><div class="wrap" style="max-width:980px">
<p class="eyebrow">ASK / <b>TIERED ANSWERING STACK</b></p><h1 style="font-size:clamp(28px,4vw,44px)">Ask the data a question</h1>
<p class="muted" id="status">Checking the data…</p>
<form id="f" style="margin-top:18px"><label for="q">Your question</label>
<textarea id="q" rows="3" maxlength="2000" placeholder="For example: How many ED visits were there in 2029?" required></textarea>
<div class="row"><button type="submit" class="accent" id="go">Ask</button><button type="button" class="ghost" id="new">New conversation</button><span id="busy" class="muted"></span></div></form>
<div id="examples" style="margin-top:8px"></div>
<div id="thread" style="margin-top:28px"></div></div></main>'''
    script = ('const TIERS=' + __import__('json').dumps(TIER_LABEL) + ',EXAMPLES=' + __import__('json').dumps(EXAMPLES) + ';' + r'''
let CONV=null;const $=id=>document.getElementById(id);
function table(cols,rows,n){if(!cols.length)return'';return '<div class="tablewrap"><table><thead><tr>'+cols.map(c=>'<th>'+esc(String(c).replace(/_/g,' '))+'</th>').join('')+'</tr></thead><tbody>'+
 rows.map(r=>'<tr>'+r.map(v=>'<td>'+esc(v===null?'':v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'+(n>rows.length?'<p class="muted">Showing '+rows.length+' of '+n+' rows.</p>':'')}
function part(a){return '<p style="font-size:18px;line-height:1.5">'+esc(a.headline||'')+'</p>'+table(a.columns||[],a.rows||[],a.row_count||0)}
function render(d){const el=document.createElement('article');el.className='card';el.style.marginBottom='18px';
 if(!d.answered){el.innerHTML='<p class="eyebrow">'+esc(d.question)+'</p><p class="notice">'+esc(d.message||d.error||'Not answered.')+'</p>';return el}
 const facts=Object.values(d.facts||{});
 el.innerHTML='<p class="eyebrow">'+esc(d.question)+'</p>'+(d.read_as?'<p class="muted">Read as: '+esc(d.read_as)+'</p>':'')+
 (d.sections&&d.sections.length?d.sections.map(s=>'<h3 style="margin-top:18px">'+esc(s.title)+'</h3>'+part(s)).join(''):part(d))+
 '<p class="row" style="margin-top:14px"><span class="pill '+(d.verification==='checked'?'ok':'warn')+'">'+(d.verification==='checked'?'checked by code':'not independently checked')+'</span>'+
 '<span class="pill">'+esc(TIERS[d.tier]||d.tier)+'</span><span class="pill">'+esc(d.seconds)+' s</span><span class="pill">$'+Number(d.usd).toFixed(4)+'</span></p>'+
 '<details style="margin-top:10px"><summary>How this was answered</summary>'+facts.map(f=>'<p>'+esc(f)+'</p>').join('')+
 '<ul>'+(d.limitations||[]).map(l=>'<li>'+esc(l)+'</li>').join('')+'</ul>'+(d.computation?'<pre>'+esc(d.computation)+'</pre>':'')+
 ((d.grounding||[]).length?'<pre>'+esc(d.grounding.join('\n'))+'</pre>':'')+'</details>'+
 '<details class="report" style="margin-top:10px"><summary>This answer is wrong</summary><label>What is wrong with it?</label><textarea rows="2" maxlength="2000"></textarea>'+
 '<div class="row"><button type="button" class="ghost">Report it</button><span class="muted"></span></div></details>';
 const rep=el.querySelector('.report'),aid=d.answer_id;rep.querySelector('button').onclick=async()=>{const note=rep.querySelector('textarea').value.trim(),msg=rep.querySelector('span');
  if(!note){msg.textContent='Say what is wrong, so a reviewer can check it.';return}
  try{const r=await api('/api/ask/report',{answer_id:aid,note});msg.textContent=r.message;rep.querySelector('button').disabled=true}catch(e){msg.textContent=e.message}};return el}
async function ask(q){$('go').disabled=true;const t0=Date.now();const tick=setInterval(()=>{$('busy').innerHTML='<span class="spin"></span> working… '+Math.round((Date.now()-t0)/1000)+' s'},500);
 let d;try{d=await api('/api/ask',{question:q,conversation_id:CONV})}catch(e){d={answered:false,question:q,message:e.message}}
 clearInterval(tick);$('busy').textContent='';$('go').disabled=false;if(d.conversation_id)CONV=d.conversation_id;$('thread').prepend(render(d));$('q').value='';$('q').focus()}
$('f').onsubmit=e=>{e.preventDefault();const q=$('q').value.trim();if(q)ask(q)};
$('q').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('f').requestSubmit()}});
$('new').onclick=()=>{CONV=null;$('thread').innerHTML='';$('q').focus()};
$('examples').innerHTML=EXAMPLES.map((x,i)=>'<button class="chip" data-i="'+i+'">'+esc(x)+'</button>').join('');
$('examples').onclick=e=>{const b=e.target.closest('button');if(b){$('q').value=EXAMPLES[+b.dataset.i];$('f').requestSubmit()}};
loadMe().then(async()=>{try{const s=await api('/api/ask/status');$('status').textContent=s.available?(s.patients.toLocaleString()+' synthetic patients, data as of '+s.as_of+
 (s.saved_recipes!=null?'. '+s.saved_recipes+' learned recipes answer instantly; new kinds of question take longer the first time.':'.')):'Ask is not set up on this server.';
 if(!s.available)$('go').disabled=true}catch(e){$('status').textContent=e.message}});''')
    return pages._page(f'Ask {pages.NAME}', body, script)


def report_wrong(ctx, org, uid, body, ip=None):
    """A signed-in user reports that one of their answers is wrong. The answer is looked up on the server by the id it was
    given, so a report can only name what this user was actually shown. -> (status, payload)"""
    from ..tiered import corrections
    with _lock:
        last = _answers.get((org, str(uid), str(body.get('answer_id') or '')[:32]))
    if not last: return 404, {'error': 'That answer is no longer held on the server; ask the question again and report the new answer.'}
    try:
        rid = corrections.report(last['question'], last, body.get('note'), reporter=f'{org}:{uid}', org=str(org))
    except ValueError as exc:
        return 400, {'error': str(exc)}
    ctx.auth.audit(org, uid, 'ask.reported_wrong', {'report': rid, 'tier': last['tier'], **question_ref(last['question'])}, ip=ip)
    stopped = ('The saved recipe that gave it will not answer this question for your organization until it is reviewed.' if (last['recipe'] or {}).get('bottle_id') is not None
               else 'The built-in recipe that gave it will not answer this question again until it is reviewed.' if (last['recipe'] or {}).get('recipe_id')
               else 'A reviewer will check the query that produced it.')
    return 200, {'report_id': rid, 'message': 'Thank you: the answer is reported. ' + stopped}


def reports(org, status=None):
    """This organization's reports only."""
    from ..tiered import corrections
    return {'reports': corrections.reports(status=status, org=str(org)), 'summary': corrections.summary(org=str(org))}


def resolve(ctx, org, uid, body, ip=None):
    from ..tiered import corrections
    try:
        corrections.resolve(int(body['id']), str(body.get('resolution')), f'{org}:{uid}', body.get('note') or '', org=str(org))
    except KeyError:
        return 404, {'error': 'No such report in this organization.'}
    ctx.auth.audit(org, uid, 'ask.report_resolved', {'report': int(body['id']), 'resolution': body.get('resolution')}, ip=ip)
    return 200, {'ok': True, 'summary': corrections.summary(org=str(org))}

