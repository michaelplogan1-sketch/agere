"""Cache compiled literal-ID patterns without caching evidence or verification.

Only the availability module's regex namespace is wrapped at service bootstrap.
The regular expression, flags, Unicode semantics and each searched text are exact.
An exact whole-token rejection path skips scans only for the literal-ID pattern
used by this module. Other patterns retain normal regex behavior. No source
snapshot, inference output, validity result or TTL is cached.
"""
from collections import OrderedDict,Counter
from threading import RLock
import re
from . import synthesis_availability as availability

VERSION='literal-reference-pattern-cache/1'
MAX_PATTERNS=8192

class RegexCache:
    def __init__(self,original=re,maximum=MAX_PATTERNS):
        if type(maximum) is not int or maximum<1:raise ValueError('Positive pattern-cache size required')
        self.original=original;self.maximum=maximum;self.lock=RLock();self.patterns=OrderedDict();self.counts=Counter();self.words=OrderedDict();self.literal_keys={};self.word_pattern=original.compile(r'[\w-]+')
    def __getattr__(self,key):return getattr(self.original,key)
    def search(self,pattern,string,flags=0):
        key=(pattern,flags)
        with self.lock:
            compiled=self.patterns.get(key)
            if compiled is None:
                compiled=self.original.compile(pattern,flags);self.patterns[key]=compiled;self.counts['misses']+=1
                if len(self.patterns)>self.maximum:
                    removed,_=self.patterns.popitem(last=False);self.literal_keys.pop(removed,None);self.counts['evictions']+=1
                if isinstance(pattern,str) and flags==0:
                    prefix=r'(?<![\w-])';suffix=r'(?![\w-])'
                    if pattern.startswith(prefix) and pattern.endswith(suffix):
                        inner=pattern[len(prefix):-len(suffix)];literal=inner.replace(r'\-','-')
                        if self.word_pattern.fullmatch(literal) and self.original.escape(literal)==inner:self.literal_keys[key]=literal
            else:self.patterns.move_to_end(key);self.counts['hits']+=1
            literal=self.literal_keys.get(key)
            if literal is not None and isinstance(string,str) and len(string)<=65536:
                tokens=self.words.get(string)
                if tokens is None:
                    tokens=frozenset(self.word_pattern.findall(string));self.words[string]=tokens
                    if len(self.words)>32:self.words.popitem(last=False)
                else:self.words.move_to_end(string)
                if literal not in tokens:self.counts['exact_token_rejections']+=1;return None
        return compiled.search(string)
    def status(self):
        with self.lock:return {'schema':VERSION,'entries':len(self.patterns),'maximum':self.maximum,'tokenized_text_entries':len(self.words),'counters':dict(self.counts),'evidence_or_validity_cached':False}

def install():
    if isinstance(availability.re,RegexCache):return availability.re
    availability.re=RegexCache(availability.re)
    return availability.re
