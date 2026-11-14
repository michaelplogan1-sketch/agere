"""Actual usage receipts plus explicit operating-cost assumptions."""
import json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def usage_cost(model,usage):
    prices=json.loads((ROOT/'config/agere/pricing.json').read_text())
    reported=(usage or {}).get('cost') if isinstance(usage,dict) else None
    if model not in prices and isinstance(reported,dict) and type(reported.get('total_cost')) in (int,float) and math.isfinite(reported['total_cost']) and reported['total_cost']>=0:
        return {'estimated_usd':float(reported['total_cost']),'price_checked_at':None,'source':'provider-reported usage.cost','invoice_verified':False}
    if model not in prices or not isinstance(usage,dict):return None
    if any(type(usage.get(k)) is not int or usage[k]<0 for k in ('input_tokens','output_tokens')):return None
    p=prices[model];five=hour=read=0
    if model.startswith('claude'):
        read=usage.get('cache_read_input_tokens',0);created=usage.get('cache_creation_input_tokens',0)
        cache=usage.get('cache_creation')
        if cache is not None and not isinstance(cache,dict):return None
        if cache and any(k in cache for k in ('ephemeral_5m_input_tokens','ephemeral_1h_input_tokens')):
            five=cache.get('ephemeral_5m_input_tokens',0);hour=cache.get('ephemeral_1h_input_tokens',0)
            if any(type(n) is not int or n<0 for n in (five,hour,created)):return None
            if 'cache_creation_input_tokens' in usage and created!=five+hour:return None
        else:five=created
        if any(type(n) is not int or n<0 for n in (five,hour,read,created)):return None
    prompt_tokens=usage['input_tokens']+five+hour+read
    tier=p.get('long_context') or {}
    long=bool(tier and prompt_tokens>tier['input_tokens_above'])
    if long:p={**p,**tier}
    cost=usage['input_tokens']*p['input']+usage['output_tokens']*p['output']
    if model.startswith('claude'):cost+=five*p['cache_write_5m']+hour*p['cache_write_1h']+read*p['cache_read']
    return {'estimated_usd':cost/1_000_000,'price_checked_at':prices['checked_at'],'source':p['source'],'invoice_verified':False,'prompt_tokens_including_cache':prompt_tokens,'rate_tier':'long_context' if long else 'standard'}
def total_cost(*,api_usd,local_seconds,compute_hour_usd,review_minutes,review_minute_usd):
    values=(api_usd,local_seconds,compute_hour_usd,review_minutes,review_minute_usd)
    if any(v is None for v in values):return {'status':'incomplete_cost_inputs','total_usd':None}
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values):raise ValueError('Nonnegative cost inputs required')
    return {'status':'assumption_based','total_usd':api_usd+local_seconds/3600*compute_hour_usd+review_minutes*review_minute_usd,
            'components':{'api':api_usd,'compute':local_seconds/3600*compute_hour_usd,'review':review_minutes*review_minute_usd}}
