"""Shared Wollstonecraft navigation for the product's existing page renderers."""
import html
import re
from pathlib import Path


PAGES = (
    ('ask', 'Ask your data', '/ask'),
    ('deliverables', 'Gallery', '/ask#gallery'),
    ('patients', 'Patients', '/ask#patients'),
    ('work', 'Work', '/action-center'),
    ('saved', 'Saved answers', '/ask#saved'),
    ('sources', 'Sources', '/ask#data'),
    ('review', 'Source review', '/learn'),
)


def palette_css():
    source = (Path(__file__).parent/'assets/answer_presentation.css').read_text()
    return re.search(r':root[^{}]*\{[^}]*\}', source).group()


def navigation(active, context=None, extra=''):
    context = context if re.fullmatch(r'ANS-[0-9a-f]{24}', context or '') else None
    def href(path):
        if not context:
            return path
        base, _, fragment = path.partition('#')
        return base+'?context='+context+('#'+fragment if fragment else '')
    links = ''.join('<a data-app-link="'+key+'" href="'+href(path)+'"'+
                    (' aria-current="page"' if key == active else '')+'>'+label+'</a>'
                    for key,label,path in PAGES)
    return ('<aside class="wc-shell-nav" aria-label="Main navigation">'
            '<a class="wc-shell-brand" href="/ask"><span aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="m4 8 5 16 7-12 7 12 5-16M9 24h14" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"/></svg></span>'
            '<div>Wollstonecraft<small>HEALTH INTELLIGENCE</small></div></a>'
            '<a class="wc-shell-new" href="/ask">＋ New question</a>'
            '<nav class="wc-shell-links" aria-label="Wollstonecraft">'+links+'</nav>'+extra+
            '<div class="wc-shell-foot"><b>W</b><span>Healthcare workspace<small>Synthetic data environment</small></span></div></aside>')


def wrap(page, *, active, title, context=None):
    """Add the same site chrome, leaving existing IDs, forms and scripts intact."""
    body = re.search(r'<body\b[^>]*>', page)
    if not body:
        page = re.sub(r'(<html\b[^>]*>)', r'\1<head>', page, count=1)
        page = page.replace('</style>', '</style></head><body>', 1)
        page = page.replace('</html>', '</body></html>')
    page = re.sub(r'<title>.*?</title>', '<title>Wollstonecraft · '+html.escape(title)+'</title>', page, count=1, flags=re.S)
    page = page.replace('</style>', palette_css()+SHELL_CSS+'</style>', 1)
    start = (navigation(active, context)+'<div class="wc-shell-page"><header class="wc-shell-header">'
             '<button type="button" class="wc-shell-toggle" aria-label="Open site navigation" aria-expanded="false">☰</button>'
             '<span>'+html.escape(title)+'</span><small>Wollstonecraft / Healthcare workspace</small></header>')
    page = re.sub(r'(<body\b[^>]*>)', r'\1'+start, page, count=1)
    index = page.rfind('</script>')
    if index >= 0:
        page = page[:index]+';\n'+NAV_SCRIPT+page[index:]
    else:
        page = page.replace('</body>','<script>'+NAV_SCRIPT+'</script></body>')
    return page.replace('</body>', '</div></body>')


SHELL_CSS = r'''
.wc-shell-nav{position:fixed;inset:0 auto 0 0;width:220px;background:var(--wc-sidebar);border-right:1px solid var(--wc-line);padding:25px 16px 18px;display:flex;flex-direction:column;z-index:40;color:#d8e3df;font:13px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wc-shell-brand{display:flex;gap:9px;align-items:center;color:#f6f8f5;font-size:16px;font-weight:650;letter-spacing:-.6px;text-decoration:none}.wc-shell-brand>span{width:32px;color:#dcf88c;flex:none}.wc-shell-brand svg{width:32px;height:32px}.wc-shell-brand small{display:block;font-size:7px;letter-spacing:1.3px;font-weight:500;color:#9bb3ac;margin-top:3px}.wc-shell-new{display:block;background:#244047;color:#f1f5f1;border:1px solid #3c565c;border-radius:9px;padding:11px 12px;margin:25px 0 17px;font-size:12px;text-decoration:none;min-height:44px}.wc-shell-links{display:grid;gap:4px;margin:0}.wc-shell-links a{display:flex;align-items:center;color:#b7c9c5;padding:11px 12px;min-height:44px;border-radius:8px;font-size:12px;text-decoration:none}.wc-shell-links a:hover{background:#264148;color:white}.wc-shell-links a[aria-current=page]{background:var(--wc-selected);color:var(--wc-primary-ink);font-weight:600}.wc-shell-foot{display:flex;align-items:center;gap:9px;margin-top:auto;padding-top:24px;font-size:10px}.wc-shell-foot>b{display:grid;place-items:center;width:31px;height:31px;background:var(--wc-selected);border-radius:9px;color:var(--wc-primary-ink);font:15px Georgia,serif}.wc-shell-foot small{display:block;font-size:9px;color:#a3b8b2}.wc-shell-page{margin-left:220px;min-width:0;min-height:100vh;background:var(--wc-paper);color:var(--wc-ink)}.wc-shell-header{height:64px;padding:0 32px;border-bottom:1px solid var(--wc-line);background:var(--wc-paper);display:flex;align-items:center;gap:15px;color:var(--wc-ink);font:12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wc-shell-header>small{margin-left:auto;color:var(--wc-muted);font-size:10px}.wc-shell-page>header:not(.wc-shell-header){background:var(--wc-paper);color:var(--wc-ink);padding:22px 36px;border-bottom:1px solid var(--wc-line)}.wc-shell-page>header:not(.wc-shell-header) p{color:var(--wc-muted)}.wc-shell-page>header:not(.wc-shell-header) h1{font-size:32px}.wc-shell-page>main{padding:30px 36px 50px;max-width:1400px}.wc-shell-page>main>nav:first-child{display:none}.wc-shell-page>main>h1{font:450 38px/1.2 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:-1.3px}.wc-shell-page .pill{color:var(--wc-primary-ink)}.wc-shell-page :is(button,input,select,textarea){border-color:var(--wc-line)}.wc-shell-toggle{display:none;min-height:44px;min-width:44px;border:1px solid var(--wc-line);border-radius:8px;background:white;color:var(--wc-primary-ink)}.wc-shell-nav :is(a,button):focus-visible,.wc-shell-toggle:focus-visible{outline:3px solid var(--wc-primary);outline-offset:3px}.wc-shell-nav .workspace-nav{border-top:1px solid #ffffff22;padding-top:15px;margin-top:20px}.wc-shell-nav .workspace-nav button{font-size:11px;min-height:40px;color:#b7c9c5}.wc-shell-nav .workspace-nav button[aria-current]{background:var(--wc-tint);color:var(--wc-primary-ink)}
@media(max-width:800px){.wc-shell-nav{transform:translateX(-100%);width:260px;transition:transform .18s ease}.wc-shell-open .wc-shell-nav{transform:none;box-shadow:12px 0 35px #30233c20}.wc-shell-page{margin-left:0}.wc-shell-header{padding:0 16px;gap:10px}.wc-shell-header>small{display:none}.wc-shell-toggle{display:block}.wc-shell-page>main{padding:24px 18px}.wc-shell-page>main>h1{font-size:35px}}
@media(prefers-reduced-motion:reduce){.wc-shell-nav{transition:none}}
@media print{.wc-shell-nav,.wc-shell-header{display:none!important}.wc-shell-page{margin-left:0}}
'''


NAV_SCRIPT = r'''
(()=>{const q=new URLSearchParams(location.search),context=q.get('context'),valid=/^ANS-[0-9a-f]{24}$/.test(context||'');const routes={ask:'/ask',saved:'/ask#saved',patients:'/ask#patients',sources:'/ask#data',work:'/action-center',deliverables:'/ask#gallery',review:'/learn'};
if(valid)document.querySelectorAll('[data-app-link]').forEach(a=>{const raw=routes[a.dataset.appLink];if(raw){const [path,fragment]=raw.split('#');a.href=path+'?context='+context+(fragment?'#'+fragment:'')}});
const button=document.querySelector('.wc-shell-toggle'),nav=document.querySelector('.wc-shell-nav'),mobile=matchMedia('(max-width:800px)');
const sync=()=>{if(nav)nav.inert=mobile.matches&&!document.body.classList.contains('wc-shell-open')};
if(button){button.onclick=()=>{const open=document.body.classList.toggle('wc-shell-open');button.setAttribute('aria-expanded',String(open));sync();if(open)nav.querySelector('a')?.focus()};document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('wc-shell-open')){document.body.classList.remove('wc-shell-open');button.setAttribute('aria-expanded','false');sync();button.focus()}})}mobile.addEventListener('change',sync);sync();})();
'''
