"""Warehouse bindings for existing actuarial and process-control operators."""
from collections import Counter
from datetime import date, timedelta
from statistics import mean, stdev
import math
from ...contracts import digest
from ...process_monitoring import monitor
from ..mathematics import claim_development

def source_volume(records, as_of):
    cutoff = date.fromisoformat(as_of)
    next_month = (cutoff.replace(day=28)+timedelta(days=4)).replace(day=1)
    last = cutoff.strftime('%Y-%m') if cutoff == next_month-timedelta(days=1) else (cutoff.replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
    outputs = []
    for source in ('notes', 'pharmacy_claims', 'labs'):
        rows = [r for r in records if r['source_table'] == source and (r.get('event_date') or '9999')[:7] <= last]
        counts = Counter(r['event_date'][:7] for r in rows)
        if not counts:
            outputs.append({'source': source, 'status': 'no_observed_records'}); continue
        month = date.fromisoformat(min(counts)+'-01'); months = []
        while month.strftime('%Y-%m') <= last:
            months.append(month.strftime('%Y-%m')); month = (month.replace(day=28)+timedelta(days=4)).replace(day=1)
        values = [counts[m] for m in months]
        result = {'source': source, 'months': months, 'record_counts': values,
                  'reference_months': months[:6], 'input_record_count': len(rows),
                  'input_record_digest': digest(sorted(r['record_id'] for r in rows)),
                  'reference_basis': 'First six complete observed months; engineering assumption, not verified stable coverage.',
                  'denominator': 'Selected snapshot roster; changing enrollment/source capture is unadjusted.'}
        if len(values) < 8 or stdev(values[:6]) == 0:
            result['status'] = 'insufficient_variable_reference'
        else:
            result.update(monitor(values[6:], reference_mean=mean(values[:6]), reference_sd=stdev(values[:6])))
            for point in result['points']: point['month'] = months[point['index']+6]
        outputs.append(result)
    return {'sources': outputs, 'date_basis': 'source_event',
            'records_with_unknown_source_date':sum(not r.get('event_date') for r in records if r['source_table'] in ('notes','pharmacy_claims','labs')),
            'cause_established': False,
            'clinical_deterioration_established': False}

def claims_runout(records, as_of):
    rows = [r for r in records if r['source_table'] == 'medical_claims']; valid, excluded = [], []
    for r in rows:
        try:
            for k in ('service_date', 'paid_date', 'available_date'): date.fromisoformat(r[k])
            if not isinstance(r.get('lines'),list) or not r['lines']:raise ValueError('Missing claim lines')
            if any(type(line.get('payer_paid_usd')) not in (int,float) or not math.isfinite(line['payer_paid_usd']) for line in r['lines']):
                raise ValueError('Missing/nonfinite paid amount')
            # Existing primitive validates amounts; validate each record separately to expose exclusions.
            claim_development([r], as_of)
        except (KeyError, TypeError, ValueError, OverflowError):
            excluded.append({'record_id': r['record_id'], 'reason': 'missing_or_invalid_claim_dates_or_amounts'}); continue
        valid.append(r)
    result = claim_development(valid, as_of)
    result.update(input_record_count=len(valid), input_record_digest=digest(sorted(r['record_id'] for r in valid)),
                  excluded_records=excluded, source_record_examples=[
                      {'record_id': r['record_id'], 'record_hash': digest(r),
                       'service_date': r['service_date'], 'paid_date': r['paid_date'],
                       'available_date': r['available_date']} for r in valid[:10]])
    return result

def calculate(operator, records, as_of):
    if operator == 'source_volume_monitor': return source_volume(records, as_of)
    if operator == 'claims_runout': return claims_runout(records, as_of)
    raise ValueError('Unregistered population mathematics')
