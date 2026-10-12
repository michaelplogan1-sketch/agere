"""Actual native/scan extraction into provenance-preserving document envelopes."""
import hashlib,json,subprocess
from pathlib import Path
from ..contracts import digest

ROOT=Path(__file__).resolve().parents[2]

def extract_pdf(path,output):
    path=Path(path).resolve();output=Path(output)
    if path.suffix.lower()!='.pdf':raise ValueError('PDF input required')
    with path.open('rb') as stream: source_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    binary=ROOT/'native/agere-ocr';source=ROOT/'native/ExtractDocument.swift'
    if not binary.exists() or binary.stat().st_mtime<source.stat().st_mtime:
        subprocess.run(['swiftc',str(source),'-o',str(binary)],check=True,timeout=180,capture_output=True)
    result=json.loads(subprocess.run([str(binary),str(path)],check=True,capture_output=True,text=True,timeout=180).stdout)
    result.update(source_sha256=source_hash,extractor_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  extraction_policy='Native text when at least 80 characters; otherwise OCR rendered page. Mixed pages may require explicit re-OCR.',
                  patient_identity_verified=False)
    for page in result['pages']:
        page['text_sha256']=hashlib.sha256(page['text'].encode()).hexdigest()
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2))
    return result
