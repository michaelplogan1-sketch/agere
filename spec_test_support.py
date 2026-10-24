"""Shared fixtures for the question-spec tests: a tiny independent warehouse and fake providers (no model, no network).

The warehouse has living and deceased patients, diabetes and heart-failure evidence, statin / SGLT2 fills in and out of the 12 months ending
2030-05-27, and a notes table the fake note reader answers for.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

from clinical_core.agere.clinical import request_plan as R

AS_OF = '2030-05-27'


def build_warehouse(path):
    path = Path(path)
    with sqlite3.connect(path / 'semantic.sqlite3') as db:
        db.executescript(f'''
            CREATE TABLE meta(key,value);
            INSERT INTO meta VALUES('version','fixture'),('source_digest','spec-fixture-1'),('as_of','{AS_OF}');
            CREATE TABLE patient(patient_id,birth_date,sex_reported,coverage_type,payer_id,pcp_clinician_id,pcp_practice_id);
            INSERT INTO patient VALUES
              ('SYN-A','1960-05-27','F','medicare','P1','DR1','ORG1'),('SYN-B','1965-05-27','M','commercial','P1','DR1','ORG1'),
              ('SYN-C','1950-05-27','F','medicare','P2','DR2','ORG2'),('SYN-D','1971-05-27','M','medicaid','P2','DR2','ORG2'),
              ('SYN-E','1940-05-27','F','medicare','P3','DR3','ORG3');
            CREATE TABLE death(patient_id,death_date);
            INSERT INTO death VALUES('SYN-E','2029-11-02');
            CREATE TABLE condition(patient_id,concept,is_diagnosis);
            INSERT INTO condition VALUES('SYN-A','diabetes',1),('SYN-B','diabetes',1),('SYN-C','diabetes',1),('SYN-C','heart_failure',1),
              ('SYN-D','heart_failure',1),('SYN-E','diabetes',1),('SYN-E','heart_failure',1),('SYN-A','heart_failure',1);
            CREATE TABLE medication(patient_id,drug_class,evidence DEFAULT 'dispensed',status DEFAULT 'dispensed',fill_reversed DEFAULT 0,event_date);
            INSERT INTO medication(patient_id,drug_class,event_date) VALUES('SYN-A','statin','2029-08-01'),('SYN-B','statin','2028-01-01'),
              ('SYN-C','sglt2_inhibitor','2029-09-09'),('SYN-D','statin','2029-10-10');
            CREATE TABLE fill(patient_id,drug_class,fill_date);
            INSERT INTO fill VALUES('SYN-A','statin','2029-08-01'),('SYN-B','statin','2028-01-01'),('SYN-C','sglt2_inhibitor','2029-09-09'),('SYN-D','statin','2029-10-10');
            CREATE TABLE utilization_event(patient_id,kind,event_date,sources,in_ehr);
            INSERT INTO utilization_event VALUES('SYN-A','ed_visit','2029-01-02','ehr',1),('SYN-A','ed_visit','2029-03-03','claims',0),
              ('SYN-B','ed_visit','2029-01-01','ehr',1),('SYN-C','inpatient','2029-02-02','adt',0);
            CREATE TABLE clinical_note(record_id,patient_id,note_type,note_date);
            INSERT INTO clinical_note VALUES('N1','SYN-A','progress','2029-09-01'),('N2','SYN-B','progress','2029-10-01'),('N3','SYN-C','progress','2029-11-01');
        ''')
    return path


class Warehouse:
    """A temp warehouse + catalog + stack stand-in (add .live() for allow_live)."""

    def __init__(self, allow_live=False):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = build_warehouse(self.tmp.name)
        self.catalog = R.Catalog.load(self.path)
        self.stack = SimpleNamespace(path=self.path, allow_live=allow_live, receipts=None, shared_request_planning=True,
                                     retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}}))

    def close(self):
        self.tmp.cleanup()


class FakeS1:
    """System One decide() stand-in. answers: callable(prompt, options, evidence) -> (label, p) or a dict by substring of the prompt/evidence."""

    def __init__(self, answers=None, default=('yes', 0.95)):
        self.answers, self.default, self.calls = answers or {}, default, []

    def __call__(self, question, options, evidence, *, as_of, threshold=0.8, task_key=None, ledger=None, allow_live=True, **kw):
        text = ' '.join(e.get('text', '') for e in evidence) or question  # the part's own sentence decides, as the real provider is asked to
        self.calls.append({'task': task_key, 'prompt': question, 'options': list(options), 'evidence': [e.get('text') for e in evidence]})
        pick = self.default
        if callable(self.answers):
            pick = self.answers(question, options, evidence) or self.default
        else:
            for needle, answer in self.answers.items():
                if needle in text:
                    pick = answer
                    break
        label, p = pick
        rest = (1 - p) / max(1, len(options))
        probs = {k: (p if k == label else rest) for k in options}
        probs.setdefault(label, p)
        decided = 'jev' if p >= threshold else None
        return {'label': label, 'probabilities': probs, 'calibrated_probabilities': probs, 'provider': 'jev', 'decided_by': decided,
                'cost_usd': 0.0002, 'steps': [{'provider': 'jev', 'status': 'answered', 'label': label, 'cost_usd': 0.0002, 'cache_hit': False}],
                'cache_hit': False, 'request_hash': 'h', 'task_key': task_key}


class FakeModel:
    """A Claude stand-in: replies is a list of strings (or callables(system, user)) returned in order; records every call."""

    def __init__(self, replies, name='claude-haiku-5-5', usd=0.01, delay=0.0):
        self.replies, self.name, self.usd, self.delay, self.calls = list(replies), name, usd, delay, []
        self.hosted = True

    def __call__(self, system, user, max_tokens=700, temperature=0.0, seed=None):
        import time
        if self.delay: time.sleep(self.delay)
        self.calls.append({'system': system, 'user': user, 'max_tokens': max_tokens})
        reply = self.replies.pop(0) if self.replies else '{}'
        text = reply(system, user) if callable(reply) else reply
        if not isinstance(text, str): text = json.dumps(text)
        return text, {'model': self.name, 'api_usd': self.usd, 'cache_hit': False, 'api_cost_known': True, 'seconds': 0.01}


FQ = 'Patients, heart failure, how many?'  # a wording the grammar does not read but a spec can: the paid fillers' territory


def spec_count_hf(question, source='patients', measure='How many'):
    """A valid spec for '... heart failure ...' wording (the typed request, quotes taken from the question)."""
    return {'source': {'table': 'patient', 'quote': source},
            'select': [{'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': measure}],
            'where': {'op': 'concept', 'kind': 'condition', 'names': ['heart_failure'], 'version': 1, 'period': None, 'quote': 'heart failure'},
            'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
