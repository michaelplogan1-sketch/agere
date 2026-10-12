"""Pinned source-schema extension, separate from clinical predicates and models."""
from pathlib import Path
import hashlib,json,re
PATH=Path(__file__).resolve().parents[2]/'config/agere/received-source-catalog-v123.json'


def load():
 data=json.loads(PATH.read_text())
 if data.get('schema')!='received-source-catalog/1' or data.get('synthetic') is not True:raise ValueError('Pinned synthetic received-source catalog required')
 for table,spec in data['definitions'].items():
  if not re.fullmatch('[a-z][a-z0-9_]*',table) or set(spec)!={'domain','relation','fields','links','narrative_fields','explicit_clinical_clock_fields'}:raise ValueError('Invalid source contract')
  if not set(spec['narrative_fields'])<=set(spec['fields']) or not set(spec['explicit_clinical_clock_fields'])<=set(spec['fields']):raise ValueError('Source field binding unavailable')
  if any(not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',f) for f in spec['fields']):raise ValueError('Literal field names required')
 return data['definitions']


def identity():return hashlib.sha256(PATH.read_bytes()).hexdigest()
DEFINITIONS=load()
