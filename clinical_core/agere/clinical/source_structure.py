"""Exact-span document structure, independent of clinical interpretation.

Only explicit formatting establishes a role. An unrecognised header stays unknown;
these rules do not infer care, a subject, or a clinical date from nearby text.
"""
from pathlib import Path
import hashlib
import re

VERSION = 'source-structure/1'
HEADINGS = {
    'signature': 'signature', 'signatures': 'signature', 'signed by': 'signature', 'signed': 'signature',
    'electronically signed by': 'signature', 'author': 'metadata',
    'document information': 'metadata', 'document metadata': 'metadata',
    'family history': 'family_history', 'fh': 'family_history',
    'medications': 'medication_list', 'medication list': 'medication_list',
    'current medications': 'medication_list', 'medication reconciliation': 'medication_list',
    'past medical history': 'history', 'medical history': 'history', 'pmh': 'history',
    'copied history': 'copied_history', 'copy-forward history': 'copied_history',
    'copied forward': 'copied_history',
    'assessment': 'assessment', 'assessment and plan': 'assessment_plan',
    'assessment & plan': 'assessment_plan', 'a/p': 'assessment_plan',
    'plan': 'plan', 'history of present illness': 'body', 'hpi': 'body',
    'subjective': 'body', 'objective': 'body', 'interval history': 'body',
    'progress note': 'body', 'clinical course': 'body', 'hospital course': 'body',
    'addendum': 'body', 'patient instructions': 'instructions',
}
SIGNATURE = re.compile(r'^\s*(?:electronically\s+signed(?:\s+by)?|signed\s+by|e[- ]signed\s+by|signature)\s*[:\-]', re.I)
METADATA = re.compile(r'^\s*(?:author|provider|specialty|department|document\s+id|note\s+id|patient\s+id|encounter\s+id)\s*:', re.I)
CREDENTIAL_LINE = re.compile(r"^\s*[^\n:,|]{1,100},\s*(?:MD|DO|NP|PA-C|RN|PhD)\b[. ]*(?:\|[^\n]*)?\s*$",re.I)
ATTESTATION = re.compile(r'^\s*(?:signed|electronically signed|authenticated)\s*:',re.I)


def identity():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def sections(text):
    if not isinstance(text, str): raise ValueError('Source text required')
    result = []; start = 0; current = None
    lines=text.splitlines(keepends=True); offsets=[];cursor=0
    for line in lines: offsets.append(cursor);cursor+=len(line)
    paired={}
    for i,line in enumerate(lines):
        if i and ATTESTATION.match(line):
            previous=i-1
            if not lines[previous].strip() and previous:previous-=1
            if CREDENTIAL_LINE.fullmatch(lines[previous]):
                paired[previous]={'role':'signature','start':offsets[previous],
                    'end':offsets[i]+len(line),'quote':text[offsets[previous]:offsets[i]+len(line)],
                    'basis':'credentialed_author_line_with_explicit_attestation'}
    for i,line in enumerate(lines):
        end = start + len(line); stripped = line.strip(); marker = None
        # Explicit colon or a line containing only a recognised section name.
        label = stripped.lstrip('#').strip().split(':', 1)[0].strip().casefold()
        if i in paired:
            marker=paired[i]
        elif label in HEADINGS and not METADATA.match(line) and (':' in stripped or label == stripped.lstrip('#').strip().casefold()):
            marker = {'role': HEADINGS[label], 'start': start, 'end': end,
                      'quote': line, 'basis': 'explicit_section_heading'}
        elif SIGNATURE.match(line):
            marker = {'role':'signature', 'start':start, 'end':end, 'quote':line, 'basis':'explicit_signature_marker'}
        elif re.fullmatch(r'(?:#{1,6}\s+)?[A-Za-z][A-Za-z /&-]{1,70}:', stripped):
            marker = {'role':'unknown_section', 'start':start, 'end':end, 'quote':line, 'basis':'unmapped_section_heading'}
        if marker:
            current = marker
        role = current['role'] if current else 'unsectioned'
        basis = current
        if METADATA.match(line):
            role = 'metadata'; basis = {'role':role,'start':start,'end':end,'quote':line,'basis':'explicit_metadata_field'}
        result.append({'start':start,'end':end,'role':role,'marker':basis})
        start=end
    return result


def locate(text, start, end):
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError('Exact nonempty source span required')
    rows=[r for r in sections(text) if r['start'] < end and start < r['end']]
    roles={r['role'] for r in rows}
    role=next(iter(roles)) if len(roles)==1 else 'cross_section'
    markers=[]
    for r in rows:
        if r['marker'] and r['marker'] not in markers: markers.append(r['marker'])
    return {'schema':VERSION,'identity':identity(),'role':role,'target_start':start,'target_end':end,
            'target_quote':text[start:end], 'markers':markers,
            'clinical_account_excluded':role in ('metadata','signature','instructions'),
            'interpretation_required':role not in ('metadata','signature','instructions'),
            'limits':'Formatting describes source role only; it does not establish a patient event or validate clinical meaning.'}


def exclusion(assertion):
    context=assertion.get('source_structure') or {}
    if context.get('clinical_account_excluded'):
        return 'nonclinical_source_role'
    return None
