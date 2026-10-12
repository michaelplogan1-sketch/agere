"""Explicit token-cost accounting; rates are arguments, not hidden constants."""
import math


def estimate_cost(*,uncached_input_tokens,cached_input_tokens,output_tokens,
                  input_per_million,cached_per_million,output_per_million,
                  cache_write_tokens=0,cache_write_per_million=0,
                  local_compute_usd=0,review_usd=0):
    tokens=[uncached_input_tokens,cached_input_tokens,output_tokens,cache_write_tokens]
    if any(type(x) is not int or x<0 for x in tokens):raise ValueError('Token counts must be nonnegative integers')
    rates=[input_per_million,cached_per_million,output_per_million,cache_write_per_million,local_compute_usd,review_usd]
    if any(not math.isfinite(x) or x<0 for x in rates):raise ValueError('Costs must be finite and nonnegative')
    api=sum(n*p for n,p in zip(tokens,rates[:4]))/1_000_000
    return {'estimated_api_usd':api,'estimated_total_usd':api+local_compute_usd+review_usd,
            'scope':'Scenario estimate, not an invoice; categories must be disjoint and counted with the target provider tokenizer.'}


def cache_break_even(*,normal_rate,write_rate,read_rate):
    """Minimum identical calls for cache cost <= uncached cost, TTL permitting."""
    if not all(math.isfinite(v) and v>=0 for v in (normal_rate,write_rate,read_rate)):
        raise ValueError('Invalid cache rates')
    if write_rate<=normal_rate:return 1
    if read_rate>=normal_rate:return None
    return max(1,math.ceil((write_rate-read_rate)/(normal_rate-read_rate)))
