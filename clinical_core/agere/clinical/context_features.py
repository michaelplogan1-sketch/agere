"""Interpretable target-context features, not rule-based clinical truth.

Cue spans are exact substrings. Learned heads decide how to use features; every
output remains an annotation proposal. No date is silently promoted to current.
"""
import re
from sklearn.base import BaseEstimator, TransformerMixin
from .scoped_reader import scoped_view, SelectView

VERSION = 'target-context-features/1'
CUES = {
    'negation': r'\b(?:no|not|denies|denied|without|negative for|neither|never)\b',
    'uncertainty': r'\b(?:possible|possibly|suspected|may have|might have|rule out|unclear|unconfirmed|not (?:verified|confirmed|established)|cannot (?:confirm|exclude))\b',
    'retraction': r'\b(?:entered in error|retracted|incorrect diagnosis|removed as erroneous|wrong patient)\b',
    'historical': r'\b(?:history of|prior|previous|last year|historical|copied|past episode|previously)\b',
    'current': r'\b(?:today|currently|current|now|this encounter|this visit|presently)\b',
    'future': r'\b(?:planned|proposed|scheduled|next week|will|tomorrow)\b',
    'relative': r'\b(?:mother|father|sister|brother|daughter|son|spouse|relative|family history)\b',
    'request': r'\b(?:requested|request|obtain|fax|release of information)\b',
    'completion': r'\b(?:attended|completed|seen by|visited|consultation)\b',
    'list': r'\b(?:list|listed|regimen|reconciliation|reconciled)\b',
    'order': r'\b(?:ordered|order|prescribed|prescription|renewed)\b',
    'dispense': r'\b(?:dispensed|fill|filled|supplied|pharmacy history|days supplied)\b',
    'use': r'\b(?:taking|takes|using|use|adherence)\b',
    'administration': r'\b(?:administered|given|infused)\b',
    'hold': r'\b(?:held|hold|stopped|discontinued|withheld|declined)\b',
    'coverage': r'\b(?:records|unavailable|incomplete|unreconciled|missing|not received)\b',
}
CONTRAST = re.compile(r'\b(?:but|however|although|yet|except)\b', re.I)


def context_view(x):
    base = scoped_view(x)
    text=x['text']; a=x['target_start']; b=x['target_end']
    lo=base['trace']['focus_start']; hi=base['trace']['focus_end']
    # Adversative conjunctions separate polarity. Preserve offsets; never split
    # within a target. These boundaries remain heuristic, not a parse tree.
    for m in CONTRAST.finditer(text,lo,hi):
        if m.end() <= a:lo=m.end()
        elif m.start() >= b:hi=m.start();break
    while lo<a and text[lo].isspace():lo+=1
    while hi>b and text[hi-1].isspace():hi-=1
    local=text[lo:hi]; left=text[lo:a]; right=text[b:hi]
    prefix=text[:lo]
    heading=None
    # The last explicit heading governs; do not carry all previous sections.
    for m in re.finditer(r'(?m)^\s*([^\n:]{1,90}):\s*(?=\n|$)',prefix):
        heading={'start':m.start(1),'end':m.end(1),'text':m[1]}
    copy_headers=list(re.finditer(r'(?im)^\s*(?:prior note|copied (?:from|note|history))[^\n:]{0,65}:',prefix))
    copy_header=copy_headers[-1] if copy_headers else None
    if heading and copy_header and heading['start']>copy_header.end():
        # A new dated/current section can end copy context; uncertain headings
        # do not prove the copied block has ended.
        if re.search(r'(?i)\b(?:today|current|new encounter)\b',heading['text']):copy_header=None
    features={}; spans=[]
    for name,pattern in CUES.items():
        matches=list(re.finditer(pattern,local,re.I))
        features[name+':local']=float(bool(matches))
        for side in ('left','target','right'):
            selected=[m for m in matches if ('left' if lo+m.end()<=a else 'right' if lo+m.start()>=b else 'target')==side]
            features[name+':'+side]=float(bool(selected))
        for m in matches:
            spans.append({'kind':name,'start':lo+m.start(),'end':lo+m.end(),'quote':m[0]})
    features['negation:coordinated']=float(bool(re.search(r'\b(?:no|neither|denies|without)\b[^.;\n]{0,90}\b(?:or|nor|and)\s+[^.;\n]*$',left,re.I)))
    features['negation:pseudo']=float(bool(re.search(r'\b(?:not only|no (?:change|increase|decrease)|without (?:change|difficulty))\b',local,re.I)))
    features['negation:unrelated_outside']=float(bool(re.search(CUES['negation'],text[:lo]+' '+text[hi:],re.I)))
    features['negation:after_reporting']=float(bool(re.search(r'\b(?:reported\s+(?:false|no)|denied|absent)\b',right,re.I)))
    features['list:problem']=float(bool(re.search(r'\b(?:problem list|problems|diagnoses|assessment)\s*:',local,re.I)))
    features['metadata:signature']=float(bool(re.search(r'\b(?:MD|DO|NP|RN|PharmD)\s*\|',local)))
    features['metadata:bare_heading']=float(local.rstrip().endswith(':') and len(local)<100)
    features['historical:copy_heading']=float(copy_header is not None)
    features['historical:active_heading']=float(bool(heading and re.search(CUES['historical'],heading['text'],re.I)))
    features['current:active_heading']=float(bool(heading and re.search(CUES['current'],heading['text'],re.I)))
    features['short_fragment']=float(len(local.split())<=8)
    features['window:prefix_omitted']=float(bool(x.get('prefix_omitted')))
    features['window:suffix_omitted']=float(bool(x.get('suffix_omitted')))
    section=heading['text'] if heading else 'no explicit section'
    if copy_header:section+=' '+copy_header[0]
    base.update(focus=left+' TARGET_ENTITY '+right,left=left or 'no left words',right=right or 'no right words',section=section,flags=features)
    base['trace']={'focus_start':lo,'focus_end':hi,'focus_text':local,'active_heading':heading,
        'copy_heading':{'start':copy_header.start(),'end':copy_header.end(),'text':copy_header[0]} if copy_header else None,
        'cue_spans':spans,'scope_verified':False,'limitation':'Heuristic target cues; ambiguous dependencies, copy boundaries and omissions remain unresolved.'}
    return base


class ContextViews(BaseEstimator,TransformerMixin):
    def fit(self,x,y=None):return self
    def transform(self,x):return [context_view(v) for v in x]


def pipeline():
    from sklearn.pipeline import Pipeline,FeatureUnion
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.linear_model import LogisticRegression
    views=[]
    for view in ('focus','target','section','context','left','right'):
        views.append((view,Pipeline([('select',SelectView(view)),('tfidf',TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),max_features=25000,sublinear_tf=True))])))
    views.append(('flags',Pipeline([('select',SelectView('flags')),('vectorizer',DictVectorizer())])))
    return Pipeline([('views',ContextViews()),('features',FeatureUnion(views,transformer_weights={
        'focus':2.,'target':.5,'section':1.,'context':.2,'left':.75,'right':.75,'flags':1.})),
        ('classifier',LogisticRegression(C=4.,class_weight='balanced',max_iter=2000,random_state=813))])
