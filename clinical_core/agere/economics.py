"""Actual usage receipts plus explicit operating-cost assumptions."""
import json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def usage_cost(model,usage):
    prices=json.loads((ROOT/'config/agere/pricing.json').read_text())
    if model not in prices or not isinstance(usage,dict):return None
    for key in ('input_tokens','output_tokens'):
        if type(usage.get(key))!=int or usage[key]<0:return None
    p=prices[model];cost=usage['input_tokens']*p['input']+usage['output_tokens']*p['output']
    if model.startswith('claude'):
        cache=usage.get('cache_creation',{})
        five=cache.get('ephemeral_5m_input_tokens',usage.get('cache_creation_input_tokens',0));hour=cache.get('ephemeral_1h_input_tokens',0)
        cost+=five*p['cache_write_5m']+hour*p['cache_write_1h']+usage.get('cache_read_input_tokens',0)*p['cache_read']
    return {'estimated_usd':cost/1_000_000,'price_checked_at':prices['checked_at'],'source':p['source'],'invoice_verified':False}

def total_cost(*,api_usd,local_seconds,compute_hour_usd,review_minutes,review_minute_usd):
    values=(api_usd,local_seconds,compute_hour_usd,review_minutes,review_minute_usd)
    if any(v is None for v in values):return {'status':'incomplete_cost_inputs','total_usd':None}
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values):raise ValueError('Nonnegative cost inputs required')
    return {'status':'assumption_based','total_usd':api_usd+local_seconds/3600*compute_hour_usd+review_minutes*review_minute_usd,
            'components':{'api':api_usd,'compute':local_seconds/3600*compute_hour_usd,'review':review_minutes*review_minute_usd}}
