"""Customer definitions: a health system's own certified definitions, loaded once at onboarding and enforced on every answer.

Against an independently written test (proof_v13) every approach, Agere and frontier models alike, was mostly "wrong" for one
reason: the analyst's definitions were not the warehouse's. Which facilities are ours, how a stay is merged across feeds, who
counts as a member, which claims are duplicates: each hospital decides these, and its finance and quality teams certify
numbers on them. A general assistant re-decides them on every question. Agere holds them.

A definitions file (config/agere/customer/*.json, schema 'agere-customer-definitions/1'):
  {"schema": "agere-customer-definitions/1", "organization": "...",
   "definitions": [
     {"id": "inpatient_admission",                      short, unique
      "name": "Inpatient admission",                    shown in answers
      "applies_when": "<regex on the question>",        which questions it governs (case-insensitive)
      "text": "<the definition in plain words, as the customer certifies it>",
      "view": {"name": "cust_inpatient_admission",      optional: the definition as a read-only SELECT over the warehouse
               "sql": "SELECT ...",                     tables, created on every connection so a query reads it
               "columns": "<what each column means>"}}]}
The customer's definitions come first and override the built-in governed definitions where they differ; a view's name must
start with cust_. A view that does not run is skipped and reported (customer.problems()), never half-installed.

    install(db)          create the customer's views on a connection (store.connect)
    relevant(question)   -> [(name, text)] the customer definitions this question calls for
    view_names()         -> the cust_ views, shown to the SQL writer with the layer's tables
"""
import json
import re
import sqlite3
from pathlib import Path

DIRECTORY = Path('config/agere/customer')
SCHEMA = 'agere-customer-definitions/1'
_problems = []


def definitions():
    out = []
    for f in sorted(DIRECTORY.glob('*.json')) if DIRECTORY.is_dir() else []:
        try:
            c = json.loads(f.read_text())
        except (OSError, ValueError) as exc:
            _problems.append(f'{f.name}: not readable ({exc})'); continue
        if c.get('schema') != SCHEMA: continue
        for d in c.get('definitions') or []:
            if not isinstance(d, dict) or not d.get('id') or not d.get('text'): continue
            try:
                re.compile(d.get('applies_when') or r'(?!)')
            except re.error:
                _problems.append(f"{d['id']}: applies_when is not a valid pattern"); continue
            out.append(d)
    return out


def view_names():
    return [d['view']['name'] for d in definitions() if isinstance(d.get('view'), dict) and str(d['view'].get('name', '')).startswith('cust_')]


def install(db):
    """Create each customer view as a TEMP VIEW. A view must be one read-only SELECT and named cust_*; one that fails is skipped."""
    for d in definitions():
        v = d.get('view')
        if not isinstance(v, dict): continue
        name, sql = str(v.get('name', '')), str(v.get('sql', '')).strip().rstrip(';')
        # A view's body can only be a SELECT (SQLite refuses anything else) and connections are read-only, so no keyword list:
        # one rejected REPLACE(), a text function, in 11 of an evaluator's 20 definitions (proof_v14).
        if not re.fullmatch(r'cust_[a-z0-9_]+', name) or ';' in sql or not re.match(r'(?is)^\s*(select|with)\b', sql):
            _problems.append(f"{d['id']}: view must be named cust_* and be one SELECT"); continue
        try:
            db.execute(f'CREATE TEMP VIEW {name} AS {sql}')
            db.execute(f'SELECT * FROM {name} LIMIT 0').fetchall()
        except sqlite3.Error as exc:
            _problems.append(f"{d['id']}: view {name} does not run ({exc})")
            try:
                db.execute(f'DROP VIEW IF EXISTS temp.{name}')
            except sqlite3.Error:
                pass


def relevant(question):
    q = ' ' + str(question) + ' '
    out = []
    for d in definitions():
        if re.search(d.get('applies_when') or r'(?!)', q, re.I):
            text = d['text']
            v = d.get('view')
            if isinstance(v, dict) and v.get('name'):
                text += f" Read it from {v['name']}" + (f" ({v['columns']})" if v.get('columns') else '') + '.'
            out.append((d.get('name') or d['id'], text))
    return out


def problems():
    return list(dict.fromkeys(_problems))
