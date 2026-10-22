"""Referral / billing network graph for collusion, kickback and patient-steering review ('provider_network_graph').

The existing 'network' operation answers who our patients use, hubs, leakage and steering arithmetic. This engine looks at the
same providers as a DIRECTED referral graph and a billing graph and asks payment-integrity questions: whose referrals are
unusually concentrated on one destination, which referrer -> destination pairs carry far more patients than the destination's
market share predicts, which pairs refer to each other, who sends referred patients out of network, and which billing NPIs bill
for rendering clinicians of many unrelated specialties from other organisations.

Referral edges (one per patient x destination clinician, at the patient's first claim with that destination in the window):
  referral_record     referrals stream requesting_clinician_id -> receiving_clinician_id (explicit referrals)
  pcp_sequence        the patient's PCP (patient.pcp_clinician_id) had a claim within referral_days before the first
                      destination claim (attributed-PCP proxy)
  handoff             otherwise the most recent different rendering clinician within referral_days (care-sequence proxy)
Facility-based specialties (radiology, anaesthesiology, pathology, emergency medicine, hospitalist) are neither referrers nor
destinations. Claims: payer_claim rows that count in metrics (finals, replacements over originals, voids/duplicates out),
rendering NPI -> clinician (specialty, organisation), billing NPI -> organisation.

Statistics: pair excess = referrals k from r to d vs the leave-r-out market share of d among all referrals into d's specialty,
binomial upper tail, lift = k / expected; referrer HHI (network.hhi, 0-10,000) and top-destination share; out-of-network
steering = binomial upper tail of the referrer's out-of-network destination claims against the overall referral rate;
reciprocity = pairs with >= min_pair referrals in both directions; billing fan-in = distinct rendering specialties and share of
renderers whose own organisation NPI differs from the billing NPI. All p-values of a family enter one Benjamini-Hochberg
procedure. Directed PageRank and Louvain communities (network.pagerank / network.louvain) place each referrer in the graph.
Flags are prompts for a compliance reviewer; none is a finding of a kickback, self-referral violation or fraud.
"""
from collections import defaultdict, Counter
from datetime import date
import re

import numpy as np
from scipy.stats import binom

from . import revenue_common as rc

VERSION = 'provider-network-graph/1'
OP = 'provider_network_graph'
FOCUS = ('all', 'referrers', 'pairs', 'reciprocity', 'steering', 'billing')
PRIMARY_CARE = {'family medicine', 'internal medicine', 'pediatrics', 'family nurse practitioner', 'physician assistant',
                'general practice', 'geriatric medicine', 'geriatrics', 'nurse practitioner'}
FACILITY_BASED = {'radiology', 'anesthesiology', 'pathology', 'emergency medicine', 'hospitalist', 'clinical laboratory'}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'REFERRAL / BILLING NETWORK INTEGRITY: directed referral graph (explicit referrals, PCP and care-sequence proxies) '
                  'with concentrated referrers (HHI, top-destination share), excess referrer->destination pairs vs market share, '
                  'reciprocal referral pairs, out-of-network steering and billing-NPI fan-in across unrelated specialties - '
                  'kickback / self-referral / steering review prompts.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'focus': '|'.join(FOCUS), 'months': 24, 'referral_days': 90, 'min_pair': 5, 'min_referrals': 10,
                           'alpha': 0.05, 'specialty': '', 'top': 50},
            'meaning': 'Directed referral edges per patient x destination clinician (referrals stream, attributed-PCP claim sequence, '
                       'care-sequence handoff within referral_days) over payer claims in the last `months` months; pair excess vs '
                       'leave-one-out destination market share (binomial, BH), referrer HHI/top share, reciprocity, out-of-network '
                       'steering (binomial, BH), billing-NPI rendering-specialty fan-in; PageRank and Louvain on the graph. Flags are '
                       'review prompts, not findings.'}
NATIVE = ('count',)


def schema(obj, string, array):
    return obj({'focus': string(FOCUS),
                'months': {'type': 'integer', 'minimum': 3, 'maximum': 60, 'description': 'Claims window in months (default 24)'},
                'referral_days': {'type': 'integer', 'minimum': 7, 'maximum': 180, 'description': 'Look-back from destination claim'},
                'min_pair': {'type': 'integer', 'minimum': 2, 'maximum': 100, 'description': 'Minimum patients on a flagged pair'},
                'min_referrals': {'type': 'integer', 'minimum': 2, 'maximum': 500, 'description': 'Minimum referrals for a referrer'},
                'alpha': {'type': 'number', 'minimum': 0.001, 'maximum': 0.2},
                'specialty': {'type': 'string', 'description': 'Destination specialty filter (e.g. Cardiology); empty = all'},
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 1000}})


def validate(p):
    allowed = {'focus', 'months', 'referral_days', 'min_pair', 'min_referrals', 'alpha', 'specialty', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('provider_network_graph parameters: ' + ', '.join(sorted(allowed)))
    out = {'focus': p.get('focus', 'all'), 'months': p.get('months', 24), 'referral_days': p.get('referral_days', 90),
           'min_pair': p.get('min_pair', 5), 'min_referrals': p.get('min_referrals', 10), 'alpha': float(p.get('alpha', 0.05)),
           'specialty': str(p.get('specialty') or '').strip(), 'top': p.get('top', 50)}
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    for k, lo, hi in (('months', 3, 60), ('referral_days', 7, 180), ('min_pair', 2, 100), ('min_referrals', 2, 500), ('top', 1, 1000)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: integer {lo}..{hi}')
    if not 0.001 <= out['alpha'] <= 0.2: raise ValueError('alpha: 0.001..0.2')
    return out


def _gap(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def load(db, keep, start, end):
    clin = {}
    for cid, org, spec, npi in db.execute('SELECT clinician_id, organization_id, specialty, npi FROM clinician'):
        clin[cid] = {'id': cid, 'org': org, 'specialty': spec or 'unknown', 'npi': str(npi) if npi else None}
    by_npi = {c['npi']: c for c in clin.values() if c['npi']}
    org_npi = {o: str(n) for o, n in db.execute('SELECT organization_id, npi FROM organization') if n}
    org_name = dict(db.execute('SELECT organization_id, display_name FROM organization'))
    pcp = {pid: c for pid, c in db.execute('SELECT patient_id, pcp_clinician_id FROM patient') if c}
    claims = defaultdict(list)
    for rid, pid, d, rnpi, bnpi, net, paid in db.execute(
            'SELECT record_id, patient_id, service_from, rendering_npi, billing_npi, network_status, paid_cents FROM payer_claim '
            'WHERE counts_in_metrics = 1 AND service_from BETWEEN ? AND ?', (start, end)):
        if keep is not None and pid not in keep: continue
        claims[pid].append((d[:10], by_npi.get(str(rnpi)) if rnpi else None, str(bnpi) if bnpi else None, net, (paid or 0) / 100, rid))
    explicit = defaultdict(dict)
    for pid, payload, ed in db.execute("SELECT patient_id, payload, event_date FROM record WHERE stream = 'referrals'"):
        if keep is not None and pid not in keep: continue
        r = rc.payload(payload)
        a, b = r.get('requesting_clinician_id') or r.get('ordering_clinician_id'), r.get('receiving_clinician_id')
        if a in clin and b in clin and a != b and (ed or '')[:10] <= end:
            explicit[pid][b] = (a, r.get('record_id'))
    return clin, by_npi, org_npi, org_name, pcp, claims, explicit


def referral_edges(clin, pcp, claims, explicit, p, start):
    """[(patient, from_id, to_id, date, source, network_status, paid, record_id)] one per patient x destination."""
    edges = []
    for pid, rows in claims.items():
        rows = sorted((r for r in rows if r[1]), key=lambda r: r[0])
        seen, last = set(), []  # last = recent (date, clinician) contacts
        mypcp = pcp.get(pid)
        for d, c, _b, net, paid, rid in rows:
            cid, spec = c['id'], c['specialty'].lower()
            if cid not in seen and spec not in FACILITY_BASED and d >= start:
                src = frm = None
                if cid in explicit.get(pid, {}):
                    frm, src = explicit[pid][cid][0], 'referral_record'
                elif mypcp and mypcp != cid and any(x == mypcp and 0 <= _gap(t, d) <= p['referral_days'] for t, x in last):
                    frm, src = mypcp, 'pcp_sequence'
                else:
                    for t, x in reversed(last):
                        if x != cid and 0 <= _gap(t, d) <= p['referral_days'] and clin[x]['specialty'].lower() not in FACILITY_BASED:
                            frm, src = x, 'handoff'; break
                if frm and frm in clin and clin[frm]['specialty'].lower() not in FACILITY_BASED:
                    edges.append((pid, frm, cid, d, src, net, paid, rid))
            seen.add(cid)
            last.append((d, cid))
            if len(last) > 30: last = last[-30:]
    return edges


def analyse(clin, org_npi, org_name, claims, edges, p):
    alpha = p['alpha']
    spec_of = lambda x: clin[x]['specialty']
    k = Counter((a, b) for _, a, b, *_ in edges)
    pair_paid = defaultdict(float); pair_recs = defaultdict(list); pair_oon = Counter()
    for pid, a, b, d, src, net, paid, rid in edges:
        pair_paid[(a, b)] += paid; pair_oon[(a, b)] += net == 'out_of_network'
        if len(pair_recs[(a, b)]) < 6: pair_recs[(a, b)].append(rid)
    into_spec = Counter(); into_dest = Counter(); r_spec = Counter()
    for (a, b), n in k.items():
        into_spec[spec_of(b)] += n; into_dest[b] += n; r_spec[(a, spec_of(b))] += n
    # pair excess vs leave-referrer-out market share of the destination within its specialty
    pairs = []
    for (a, b), n in k.items():
        s = spec_of(b)
        if p['specialty'] and s.lower() != p['specialty'].lower(): continue
        n_rs = r_spec[(a, s)]
        others_total, others_d = into_spec[s] - n_rs, into_dest[b] - n
        share = (others_d + 0.5) / (others_total + 1.0)  # Jeffreys-style continuity: a destination nobody else uses is not share 0
        exp = n_rs * share
        pv = float(binom.sf(n - 1, n_rs, min(share, 1.0))) if n_rs else 1.0
        pairs.append({'referrer': a, 'referrer_specialty': spec_of(a), 'destination': b, 'destination_specialty': s,
                      'referrals': n, 'referrer_referrals_in_specialty': n_rs, 'share_of_referrer': round(n / n_rs, 3) if n_rs else None,
                      'market_share_elsewhere': round(share, 4), 'expected': round(exp, 2), 'lift': round(n / exp, 2) if exp else None,
                      'p': pv, 'same_organization': clin[a]['org'] == clin[b]['org'],
                      'out_of_network_referrals': pair_oon[(a, b)], 'destination_paid': round(pair_paid[(a, b)], 2),
                      'record_ids': pair_recs[(a, b)]})
    tested = [x for x in pairs if x['referrals'] >= p['min_pair']]
    for x, q in zip(tested, rc.bh([x['p'] for x in tested])): x['q'] = round(q, 6)
    excess = sorted([x for x in tested if x['q'] <= alpha and (x['lift'] or 0) >= 2], key=lambda x: (x['q'], -x['referrals']))
    # referrers
    out_by = defaultdict(Counter); oon_by = Counter(); n_by = Counter()
    for pid, a, b, d, src, net, paid, rid in edges:
        out_by[a][b] += 1; n_by[a] += 1; oon_by[a] += net == 'out_of_network'
    base = sum(oon_by.values()) / max(1, sum(n_by.values()))
    from . import network
    referrers = []
    for a, dests in out_by.items():
        n = n_by[a]
        if n < p['min_referrals']: continue
        top, tn = dests.most_common(1)[0]
        same = sum(v for b, v in dests.items() if clin[b]['org'] == clin[a]['org'])
        pv = float(binom.sf(oon_by[a] - 1, n, base)) if oon_by[a] else 1.0
        referrers.append({'referrer': a, 'specialty': spec_of(a), 'organization': org_name.get(clin[a]['org'], clin[a]['org']),
                          'referrals': n, 'destinations': len(dests), 'hhi': round(network.hhi(dests) or 0),
                          'top_destination': top, 'top_destination_specialty': spec_of(top), 'top_share': round(tn / n, 3),
                          'same_organization_share': round(same / n, 3), 'out_of_network_share': round(oon_by[a] / n, 3),
                          'oon_p': pv})
    for x, q in zip(referrers, rc.bh([x['oon_p'] for x in referrers])): x['oon_q'] = round(q, 6)
    steering = sorted([x for x in referrers if x['oon_q'] <= alpha and x['out_of_network_share'] >= 2 * base and x['out_of_network_share'] > 0],
                      key=lambda x: x['oon_q'])
    # reciprocity
    recip = []
    for (a, b), n in k.items():
        if a < b and n >= p['min_pair'] and k.get((b, a), 0) >= p['min_pair']:
            recip.append({'clinician_a': a, 'specialty_a': spec_of(a), 'clinician_b': b, 'specialty_b': spec_of(b),
                          'a_to_b': n, 'b_to_a': k[(b, a)], 'same_organization': clin[a]['org'] == clin[b]['org'],
                          'record_ids': (pair_recs[(a, b)] + pair_recs[(b, a)])[:8]})
    strong = [e for e, n in k.items() if n >= p['min_pair']]
    reciprocity_index = (sum(1 for a, b in strong if k.get((b, a), 0) >= p['min_pair']) / len(strong)) if strong else None
    # billing fan-in
    bill = defaultdict(lambda: {'renderers': set(), 'specialties': set(), 'foreign': set(), 'claims': 0, 'paid': 0.0, 'records': []})
    for pid, rows in claims.items():
        for d, c, bnpi, net, paid, rid in rows:
            if not c or not bnpi: continue
            g = bill[bnpi]; g['renderers'].add(c['id']); g['specialties'].add(c['specialty']); g['claims'] += 1; g['paid'] += paid
            if org_npi.get(c['org']) != bnpi and c['npi'] != bnpi: g['foreign'].add(c['id'])
            if len(g['records']) < 6: g['records'].append(rid)
    npi_org = {v: k2 for k2, v in org_npi.items()}
    billing = []
    spec_counts = sorted(len(g['specialties']) for g in bill.values())
    for bnpi, g in bill.items():
        nr = len(g['renderers'])
        if nr < 3: continue
        pct = sum(1 for s in spec_counts if s <= len(g['specialties'])) / len(spec_counts)
        billing.append({'billing_npi': bnpi, 'billing_organization': org_name.get(npi_org.get(bnpi), npi_org.get(bnpi)),
                        'renderers': nr, 'specialties': len(g['specialties']), 'specialty_list': sorted(g['specialties'])[:8],
                        'foreign_renderer_share': round(len(g['foreign']) / nr, 3), 'claims': g['claims'], 'paid': round(g['paid'], 2),
                        'specialty_percentile': round(pct, 3), 'record_ids': g['records']})
    fan_in = sorted([x for x in billing if x['specialties'] >= 4 and x['foreign_renderer_share'] >= 0.5 and x['specialty_percentile'] >= 0.95],
                    key=lambda x: (-x['specialties'], -x['paid']))
    # graph placement
    nodes = sorted({a for a, _ in k} | {b for _, b in k})
    idx = {x: i for i, x in enumerate(nodes)}
    graph = {'nodes': len(nodes), 'edges': len(k), 'referral_edges': len(edges), 'reciprocity_index': None if reciprocity_index is None else round(reciprocity_index, 4)}
    if nodes:
        A = np.zeros((len(nodes), len(nodes)))
        for (a, b), n in k.items(): A[idx[a], idx[b]] = n
        pr = network.pagerank(A.T)  # rank flows to destinations that receive referrals
        hubs = sorted(range(len(nodes)), key=lambda i: -pr[i])[:10]
        graph['pagerank_top'] = [{'clinician': nodes[i], 'specialty': spec_of(nodes[i]), 'pagerank': round(float(pr[i]), 5),
                                  'referrals_in': int(into_dest[nodes[i]])} for i in hubs]
        if len(nodes) <= 4000:
            labels, q = network.louvain(A + A.T, seed=0)
            graph.update(communities=int(labels.max() + 1), modularity=round(float(q), 4))
            for x in referrers: x['community'] = int(labels[idx[x['referrer']]])
    return {'pairs_tested': len(tested), 'excess_pairs': excess, 'referrers': sorted(referrers, key=lambda x: (-x['hhi'], -x['referrals'])),
            'steering': steering, 'baseline_out_of_network_share': round(base, 4), 'reciprocal_pairs': recip,
            'billing_fan_in': fan_in, 'billing_npis': len(billing), 'graph': graph,
            'edge_sources': dict(Counter(e[4] for e in edges))}


def flags(a, p):
    out = []
    for x in a['excess_pairs']:
        conc = (x['share_of_referrer'] or 0) >= 0.6
        out.append({'flag': 'concentrated_referral_pair' if conc else 'excess_referral_pair', 'entity': f"{x['referrer']} -> {x['destination']}",
                    'detail': f"{x['referrals']} of {x['referrer_referrals_in_specialty']} {x['destination_specialty']} referrals "
                              f"(lift {x['lift']}x vs market share {x['market_share_elsewhere']:.1%})"
                              + ('; same organization' if x['same_organization'] else ''),
                    'probability': round(1 - x['q'], 4), 'dollars': x['destination_paid'], 'record_ids': x['record_ids']})
    for x in a['reciprocal_pairs']:
        out.append({'flag': 'reciprocal_referrals', 'entity': f"{x['clinician_a']} <-> {x['clinician_b']}",
                    'detail': f"{x['a_to_b']} and {x['b_to_a']} referrals each way", 'probability': None, 'dollars': None,
                    'record_ids': x['record_ids']})
    for x in a['steering']:
        out.append({'flag': 'out_of_network_steering', 'entity': x['referrer'],
                    'detail': f"{x['out_of_network_share']:.0%} of {x['referrals']} referrals out of network vs "
                              f"{a['baseline_out_of_network_share']:.0%} overall", 'probability': round(1 - x['oon_q'], 4), 'dollars': None,
                    'record_ids': []})
    for x in a['billing_fan_in']:
        out.append({'flag': 'billing_npi_fan_in', 'entity': x['billing_npi'],
                    'detail': f"bills for {x['renderers']} renderers in {x['specialties']} specialties, "
                              f"{x['foreign_renderer_share']:.0%} from other organizations", 'probability': None, 'dollars': x['paid'],
                    'record_ids': x['record_ids']})
    keep = {'pairs': ('concentrated_referral_pair', 'excess_referral_pair'), 'reciprocity': ('reciprocal_referrals',),
            'steering': ('out_of_network_steering',), 'billing': ('billing_npi_fan_in',)}.get(p['focus'])
    return [f for f in out if keep is None or f['flag'] in keep]


def run(db, patient_ids, p, as_of):
    start, end = rc.window(as_of, p['months'])
    from datetime import timedelta
    lb = (date.fromisoformat(start) - timedelta(days=p['referral_days'])).isoformat()
    keep = set(patient_ids) if patient_ids is not None else None
    clin, by_npi, org_npi, org_name, pcp, claims, explicit = load(db, keep, lb, end)
    edges = referral_edges(clin, pcp, claims, explicit, p, start)
    a = analyse(clin, org_npi, org_name, claims, edges, p)
    fl = flags(a, p)
    if p['focus'] == 'referrers':
        rows = a['referrers'][:p['top']]
    else:
        rows = fl[:p['top']]
    assumptions = [
        'Synthetic warehouse; referrals are mostly PROXIES: explicit referral records where received, else the attributed PCP\'s '
        f"claim within {p['referral_days']} days before the patient's first claim with the destination, else the most recent different "
        'rendering clinician in that window (care-sequence handoff). A proxy referral is a care sequence, not a documented order.',
        f"Claims window {start} to {end}; payer claims counting in metrics; facility-based specialties excluded as referrers/destinations.",
        f"Pair excess vs the destination's market share among other referrers into its specialty (binomial; BH q <= {p['alpha']}; "
        f"lift >= 2; >= {p['min_pair']} patients). Out-of-network steering vs the overall referral out-of-network share "
        f"({a['baseline_out_of_network_share']:.1%}).",
        'Billing fan-in: >= 4 rendering specialties, >= 50% renderers from another organization, top 5% of billing NPIs by specialty count.',
        'Ownership, compensation and contracts (Stark/Anti-Kickback facts) are not received: every flag is a review prompt, not a finding.']
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'period': {'start': start, 'end': end}, 'rows': rows, 'flags': fl,
            'analysis': {k2: v for k2, v in a.items() if k2 not in ('referrers',)}, 'referrers': a['referrers'][:200],
            'assumptions': assumptions,
            'method': ('Directed referral graph (referral records, attributed-PCP claim sequence, care-sequence handoff) over payer claims; '
                       'pair excess vs leave-one-out destination market share (binomial, Benjamini-Hochberg), referrer HHI and top share, '
                       'reciprocity, out-of-network steering (binomial, BH), billing-NPI rendering-specialty fan-in; directed PageRank and '
                       'Louvain communities (network.py).')}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    a = r['analysis']
    c = Counter(f['flag'] for f in r['flags'])
    g = a['graph']
    parts = [f"{g['referral_edges']:,} referrals among {g['nodes']:,} clinicians ({r['period']['start']} to {r['period']['end']})"]
    if r['parameters']['focus'] == 'referrers' and r['rows']:
        x = r['rows'][0]
        parts.append(f"most concentrated referrer {x['referrer']} ({x['specialty']}) HHI {x['hhi']:,}, {x['top_share']:.0%} to {x['top_destination']}")
    parts.append(', '.join(f"{v} {k.replace('_', ' ')}" for k, v in c.items()) or 'no integrity flags at the stated thresholds')
    return 'Referral network review: ' + '; '.join(parts)


def answer(report, plan, question):
    return rc.answer(report, plan, value=headline(report) + '.', table=report['rows'], number=len(report['flags']),
                     definitions=[{'term': 'referral (proxy)', 'basis': 'referral record, else attributed-PCP claim sequence, else care-sequence handoff'},
                                  {'term': 'HHI', 'basis': 'sum of squared destination shares x 10,000'}])


# ---- free local-compiler route ----------------------------------------------------------------------------------------
INTEGRITY = (r'\bkick-?backs?\b|\bcollu\w+\b|\bself-?referr\w+\b|\bstark\b|\banti-?kickback\b|\breferral (?:patterns?|rings?|schemes?|'
             r'concentration|network graph|anomal\w+|integrity)\b|\breciprocal referr\w+|\brefer(?:s|ring)? (?:to )?each other\b|'
             r'\bsteer(?:s|ing)? (?:patients|referrals) (?:out of network|to)\b|\bbilling npis?\b.*\b(?:render\w*|specialt\w+)\b|'
             r'\bbills? (?:for|under) (?:other|another|many)\b|\bconcentrated referr\w+|\bunusual(?:ly)? (?:high )?referr\w+')


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(INTEGRITY, q): return None
    if rc.knowledge_only(q): return None
    params = {}
    if re.search(r'\breciproc\w+|\beach other\b', q): params['focus'] = 'reciprocity'
    elif re.search(r'\bbilling npis?\b|\bbills? (?:for|under)\b', q): params['focus'] = 'billing'
    elif re.search(r'\bout of network\b|\bsteer\w*', q): params['focus'] = 'steering'
    elif re.search(r'\bwhich (?:pcps?|physicians?|doctors?|clinicians?|providers?|referrers?)\b.*\bconcentrat\w+|\bconcentrated referr\w+', q):
        params['focus'] = 'referrers'
    m = re.search(r'\b(?:last|past)\s+(\d{1,2})\s+months?\b', q)
    if m: params['months'] = max(3, min(60, int(m.group(1))))
    for s in ('cardiology', 'orthopedics', 'oncology', 'gastroenterology', 'neurology', 'dermatology', 'urology', 'physical therapy',
              'ophthalmology', 'nephrology', 'pulmonology', 'endocrinology', 'psychiatry', 'rheumatology'):
        if re.search(r'\b' + s + r'\b', q): params['specialty'] = s.title(); break
    return plan(OP, params, 'Referral / billing network integrity review (kickback, self-referral, steering prompts)', question,
                [f"focus {params.get('focus', 'all')}"])


TEMPLATES = ['Are there referral patterns that suggest kickbacks?', 'Which physicians refer to each other unusually often?',
             'Find possible self-referral or collusion in our provider network', 'Which PCPs have concentrated referrals to one cardiologist?',
             'Who is steering patients out of network?', 'Show billing NPIs that bill for rendering providers in many unrelated specialties',
             'Referral network graph anomalies over the last 12 months', 'Detect reciprocal referral rings among specialists']
