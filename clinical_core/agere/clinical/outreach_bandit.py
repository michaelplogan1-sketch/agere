"""Outreach learning loop (NEEDS OUTCOME DATA): Thompson sampling over outreach arms.

An arm is (outreach type, segment), e.g. ('gdmt_review', 'heart_failure') or
('care_gap_call', 'practice:WH-PRACTICE-3'). Each logged attempt is
{'arm': [type, segment], 'responded': bool} (optionally 'value' for a later
reward model). A Beta-Bernoulli posterior per arm is updated from the log; the
next batch of attempts is allocated by Thompson sampling (draw a response rate
per arm from its posterior, give the attempt to the arm with the highest draw).

Status: no outreach attempt/outcome log exists in the received warehouse. This
module is exercised only on simulated logs in tests. Its posteriors must not be
fed back into the worklist's engagement probabilities until real work logs are
recorded (future work-log integration).
"""
from collections import defaultdict
import numpy as np

VERSION = 'outreach-bandit/1'
STATUS = 'needs_outcome_data'


def learning_status(log=None):
    if not log:
        return {'schema': VERSION, 'status': STATUS,
                'detail': 'Thompson-sampling learning loop is implemented but idle: no logged outreach attempts/outcomes exist yet. '
                          'Engagement probabilities in the worklist are priors from appointment attendance, not learned response rates.'}
    b = ThompsonOutreach.from_log(log)
    return {'schema': VERSION, 'status': 'learning_from_logged_outcomes', 'attempts': len(log), 'arms': b.summary()}


class ThompsonOutreach:
    def __init__(self, arms, prior=(1.0, 1.0)):
        self.arms = [tuple(a) for a in arms]
        if len(set(self.arms)) != len(self.arms) or not self.arms: raise ValueError('distinct arms required')
        self.alpha = {a: float(prior[0]) for a in self.arms}
        self.beta = {a: float(prior[1]) for a in self.arms}

    @classmethod
    def from_log(cls, log, prior=(1.0, 1.0)):
        arms = list(dict.fromkeys(tuple(x['arm']) for x in log))
        b = cls(arms, prior)
        b.update(log)
        return b

    def update(self, log):
        for x in log:
            a = tuple(x['arm'])
            if a not in self.alpha: raise ValueError(f'unknown arm {a}')
            if x['responded']: self.alpha[a] += 1
            else: self.beta[a] += 1

    def allocate(self, attempts, rng):
        """Thompson allocation of the next `attempts` contacts across arms (one posterior draw per attempt)."""
        out = defaultdict(int)
        a = np.array([self.alpha[x] for x in self.arms]); b = np.array([self.beta[x] for x in self.arms])
        for _ in range(attempts):
            out[self.arms[int(np.argmax(rng.beta(a, b)))]] += 1
        return dict(out)

    def summary(self, draws=4000, seed=0):
        rng = np.random.default_rng(seed)
        a = np.array([self.alpha[x] for x in self.arms]); b = np.array([self.beta[x] for x in self.arms])
        samples = rng.beta(a, b, size=(draws, len(self.arms)))
        p_best = np.bincount(samples.argmax(axis=1), minlength=len(self.arms)) / draws
        from scipy.stats import beta as beta_dist
        return [{'arm': list(arm), 'attempts': int(a[i] + b[i] - 2), 'responses': int(a[i] - 1),
                 'posterior_mean': round(float(a[i] / (a[i] + b[i])), 4),
                 'ci95': [round(float(beta_dist.ppf(0.025, a[i], b[i])), 4), round(float(beta_dist.ppf(0.975, a[i], b[i])), 4)],
                 'p_best': round(float(p_best[i]), 4)} for i, arm in enumerate(self.arms)]


def simulate(true_rates, rounds=40, batch=25, seed=0, prior=(1.0, 1.0)):
    """Simulated outreach: each round allocates `batch` attempts by Thompson sampling, observes Bernoulli responses
    at the hidden true rates, and updates. Returns the bandit, the per-round allocations and cumulative regret."""
    rng = np.random.default_rng(seed)
    arms = list(true_rates)
    bandit = ThompsonOutreach(arms, prior)
    best = max(true_rates.values())
    history, regret = [], 0.0
    for _ in range(rounds):
        alloc = bandit.allocate(batch, rng)
        log = []
        for arm, n in alloc.items():
            responses = rng.random(n) < true_rates[arm]
            log += [{'arm': arm, 'responded': bool(r)} for r in responses]
            regret += n * (best - true_rates[arm])
        bandit.update(log)
        history.append(alloc)
    return bandit, history, regret
