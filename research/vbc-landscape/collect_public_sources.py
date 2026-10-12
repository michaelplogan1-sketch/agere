"""Reproducible public-source retrieval; no patient data or paid model calls.

Full texts are temporary reading material. Deliverables contain metadata,
original annotations, and links; retrieval is not an evidence-quality rating.
"""
import concurrent.futures
import hashlib
import io
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from lxml import html
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
CACHE = Path('/tmp/vbc-research-reading')
CACHE.mkdir(exist_ok=True)

def get(source):
    row = dict(source)
    started = time.monotonic()
    try:
        req = urllib.request.Request(row['url'], headers={'User-Agent': 'PublicEvidenceResearch/1.0'})
        with urllib.request.urlopen(req, timeout=35) as response:
            raw = response.read(20_000_001)
            row['resolved_url'] = response.url
            row['content_type'] = response.headers.get('Content-Type', '')
        if len(raw) > 20_000_000:
            raise ValueError('Source exceeds bounded retrieval size')
        row['sha256'] = hashlib.sha256(raw).hexdigest()
        if raw.startswith(b'%PDF'):
            pdf = PdfReader(io.BytesIO(raw))
            text = '\n'.join(f'PAGE {i+1}\n{p.extract_text() or ""}' for i,p in enumerate(pdf.pages))
            row['pages'] = len(pdf.pages)
            row['links'] = []
        else:
            doc = html.fromstring(raw)
            row['title_from_page'] = ' '.join(doc.xpath('//title/text()'))
            row['publication_metadata'] = doc.xpath('//meta[@property="article:published_time" or @name="citation_publication_date"]/@content')
            bodies = doc.xpath('//main') or doc.xpath('//article') or [doc]
            body = bodies[0]
            for el in list(body.xpath('.//script|.//style|.//nav|.//footer|.//header|.//aside')):
                if el.getparent() is not None:
                    el.getparent().remove(el)
            row['links'] = [{'label': ' '.join(a.text_content().split()), 'url': urllib.parse.urljoin(row['resolved_url'],a.get('href'))}
                            for a in body.xpath('.//a[@href]') if a.get('href') and not a.get('href').startswith('#')]
            text = '\n'.join(s.strip() for s in body.text_content().splitlines() if s.strip())
        (CACHE / (row['id']+'.txt')).write_text(text)
        row.update(retrieval_status='retrieved', text_characters=len(text), reading_file=str(CACHE / (row['id']+'.txt')),
                   review_status='retrieved_not_yet_reviewed', retrieved_on='2026-10-08')
        if len(text) < 200:
            row.update(retrieval_status='unusable_content', review_status='not_substantively_retrieved',
                       content_warning='Short application shell, challenge or unusable extraction; HTTP success is not evidence access.')
    except Exception as error:
        row.update(retrieval_status='failed', error=f'{type(error).__name__}: {error}', review_status='not_retrieved', retrieved_on='2026-10-08')
    row['retrieval_seconds'] = round(time.monotonic()-started,2)
    return row

if __name__ == '__main__':
    sources=json.loads((ROOT/'sources_seed.json').read_text())
    old_path=ROOT/'retrieval_manifest.json'
    old={r['id']:r for r in json.loads(old_path.read_text())} if old_path.exists() else {}
    missing=[s for s in sources if s['id'] not in old]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for row in executor.map(get,missing):
            old[row['id']]=row
    rows=[old[s['id']] for s in sources]
    (ROOT/'retrieval_manifest.json').write_text(json.dumps(rows,indent=2))
    for row in rows:
        print(row['id'],row['retrieval_status'],row.get('text_characters'),row.get('error',''))
