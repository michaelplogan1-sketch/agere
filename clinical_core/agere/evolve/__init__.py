"""Agere evolutionary policy search (FunSearch / AlphaEvolve style) over outreach policies, scored by the population simulator.

- sandbox.py / sandbox_worker.py  restricted execution of an evolved policy (math / statistics only, separate process,
                                  timeout, no I/O)
- features.py                     de-identified patient features from the routed plan and the simulator history (no ids)
- evaluator.py                    capacity-limited selection into quarterly waves, simulator columns per seed and wave,
                                  net savings / admissions avoided / gap closures, equity floor, train / held-out splits
- baselines.py                    router order, highest risk first, outreach.py contact value (knapsack), random
- archive.py                      MAP-Elites archive (niches: program cost x gap closures; elites per objective)
- proposer.py                     LLM mutation proposer through the ledgered sender (cached, replayable) and a fixed-list proposer
- loop.py                         the evolution loop and the held-out report with paired intervals vs baselines
- routing.py                      approved policy inside plans/router.py behind config/agere/evolve_policy.json (default off)
- run.py                          command line: python -m clinical_core.agere.evolve --semantic ... --run ...
"""
