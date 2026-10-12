"""Load a versioned local annotation reader; reject unknown contracts."""
from pathlib import Path
import hashlib,json

def load_reader(model_path,cache_path):
    card=json.loads((Path(model_path)/'model-card.json').read_text())
    if card.get('schema')=='context-evidence-reader/4':
        from .context_reader import ContextReader
        return ContextReader(model_path,cache_path)
    if card.get('schema')=='scoped-evidence-reader/3':
        from .scoped_reader import ScopedReader
        if card.get('adapter_hash'):
            from . import scoped_hybrid
            if card['adapter_hash']!=hashlib.sha256(Path(scoped_hybrid.__file__).read_bytes()).hexdigest():
                raise ValueError('Hybrid input adapter changed since packaging')
        return ScopedReader(model_path,cache_path)
    if card.get('schema')=='factorized-evidence-reader/2':
        from .evidence_reader import EvidenceReader
        return EvidenceReader(model_path,cache_path)
    raise ValueError('Unsupported reader schema')
