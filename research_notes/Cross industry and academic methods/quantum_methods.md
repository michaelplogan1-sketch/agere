# Quantum and quantum-inspired methods for hospital and health-plan analytics (maturity assessment)

Research date: 2026-11-17. Source-access legend: **[opened]** means the page itself was fetched and read. **[search summary]** means the claim comes only from search-result summaries; the page was not opened, so verify it before quoting. PMC pages returned a bot-verification (CAPTCHA) page, which was not bypassed. No PDFs were downloaded.

Agere decision rule applied throughout: an item enters only if it fixes a named buyer problem and beats a simple baseline (CP-SAT, HiGHS/Gurobi-class MILP, heuristics; for prediction, logistic regression or gradient boosting) and an LLM alone, on accuracy and cost.

## Q1. Published nurse rostering, OR scheduling and patient admission scheduling with D-Wave annealers, QAOA or hybrid solvers: sizes, quality and time vs classical

### Takeaway
The hospital-scheduling quantum literature is toy-scale. Nurse rostering on a D-Wave 2000Q was 3–4 nurses over 5–14 days, it often failed to find a fully feasible roster, and it had no classical baseline. I found no peer-reviewed OR, bed or radiotherapy scheduling study that compares a quantum or hybrid solver head-to-head with Gurobi/CP-SAT on equal footing. Recommendation: **skip** for quantum hardware. Use CP-SAT/MILP for the scheduling buyer problems.

### Cited Findings
- **Nurse scheduling on D-Wave 2000Q (Ikeda, Nakamura, Humble, 2019; arXiv 1904.12139)** [opened]
  - Size: N = 3–4 nurses, D = 5–14 days, two shifts. Hard constraints: at least 1 nurse per day and no consecutive-day work.
  - Settings: 200 μs anneal per sample, 1000 samples per instance.
  - Forward annealing found no fully constraint-satisfying schedules for N=3, D=13 or for N=4, D=10–14.
  - Reverse annealing from the best forward sample helped some cases (near 100% at N=3, D=14), but gave 0% at N=3, D=13.
  - The authors explicitly do not compare with conventional solvers.
  - The classical tabu-based qbsolv found satisfying solutions for a larger N=4, D=160 instance with the same parameters.
  - [ar5iv 1904.12139](https://ar5iv.labs.arxiv.org/html/1904.12139)
- **Staff allocation with D-Wave hybrid CQM (Ciacco et al., 2025; educational staff allocation, not hospital)** [search summary]: the solver produced balanced assignments in short runtimes. The authors say practical benefit over classical approaches "remains under active investigation". [arXiv 2510.12278](https://arxiv.org/html/2510.12278v2)
- **Unified QUBO for OR, bed and staff scheduling (ICCS 2026 paper)** [search summary]: solved with neural-guided quantum-inspired simulated annealing on synthetic instances. It is compared only with classical simulated annealing, not with MILP or CP solvers. [ICCS 2026](https://www.iccs-meeting.org/archive/iccs2026/papers/167870188.pdf)
- **Radiotherapy / proton therapy (arXiv 2506.04328)** [search summary]: this is a quantum-inspired genetic algorithm, not annealing. The authors state that a true quantum version would need qubit counts "far beyond current NISQ devices". [arXiv 2506.04328](https://arxiv.org/abs/2506.04328)
- **Classical radiotherapy admission scheduling** [search summary]: integer programming is the established method; this study compares it against first-come-first-served. Classical radiotherapy method comparisons are also on arXiv. [PMC5503264](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5503264/); [arXiv 2211.01150](https://arxiv.org/abs/2211.01150v2)
- **Closest equal-footing scheduling benchmark (transport robot scheduling, industrial, not hospital; Leib et al., Sci. Rep. 13:18743, 2023)** [opened, abstract only]
  - Compares D-Wave hybrid, Fujitsu Digital Annealer (DA), hybrid DA and Gurobi, with Gurobi as the guaranteed-optimal baseline.
  - The abstract reports "promising results for the digital annealer" and only "some opportunities" for the hybrid quantum annealer.
  - Specific numbers are not in the abstract.
  - [arXiv 2309.09736](https://arxiv.org/abs/2309.09736)
- **Resource-constrained project scheduling with quantum annealing (Sci. Rep. 2024)** [search summary]: "significant potential" for small to medium instances. Gurobi comparison not confirmed. [PMC11263701](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11263701/)
- **Production scheduling preprint** [search summary]: D-Wave Leap hybrid outperformed Gurobi on its test set. The authors concede "it is not known if it is due to utilizing quantum computing", because the hybrid solver is largely classical and proprietary. [arXiv 2408.01641](https://www.arxiv.org/pdf/2408.01641)

### Inferences
- Hospital rostering instances in practice (tens to hundreds of nurses, 4–6-week horizons, many soft constraints) are orders of magnitude larger than anything solved on pure annealing hardware.
- Penalty-based QUBO encodings handle hard constraints badly. That is the failure seen in the nurse study, and it is exactly where CP-SAT and MILP are strong.
- "Hybrid" D-Wave results cannot be attributed to the quantum part. For Agere they behave like a proprietary cloud heuristic with extra cost and lock-in, and give no capability that CP-SAT/HiGHS lacks.
- **Rating**
  - Hospital questions covered: nurse rostering, OR scheduling, patient-bed assignment, radiotherapy scheduling.
  - Data needed: staff roster, skills, contracts, demand by shift, OR block and case durations, bed census.
  - Maturity: about TRL 2–3 for quantum, TRL 9 for classical OR solvers.
  - Recommendation: **skip** quantum or hybrid annealing. Classical CP-SAT/MILP is the trial-worthy path.

### Gaps
- I found no ambulance-routing quantum study with a classical head-to-head. The search was not run deeply on this.
- I found no published QAOA study on any hospital scheduling instance at realistic size.
- The full results of Leib et al. 2023 (actual gaps and times) were not opened.

## Q2. Systematic reviews and benchmarks of quantum advantage claims in optimisation

### Takeaway
The best equal-footing benchmark (Quinton et al., NTNU; Sci. Rep. 2025) finds that D-Wave hybrid matches or beats CPLEX/Gurobi only on binary quadratic problems. It loses clearly on MILP with many constraints, which is the class that scheduling belongs to. The field's own consensus review (Abbas et al., Nature Reviews Physics 2024) does not claim demonstrated advantage and calls for fair benchmarking metrics.

### Cited Findings
- **Quinton, Myhr, Barani, Fernández del Granado, Zhang (arXiv 2409.05542 v4, 6 Oct 2025; Sci. Rep. 2025, DOI 10.1038/s41598-025-96220-2)** [opened]
  - Setup: D-Wave hybrid CQM vs CPLEX, Gurobi and IPOPT.
  - Binary linear problems with 10k–100k variables: D-Wave reached the same optimum but took about 6 s. CPLEX and Gurobi took under 1 s at the low end. No speed advantage.
  - Binary linear problems with added linear constraints: runtime exceeded 600 s with the full constraint set, and objective variability reached up to about 5×.
  - One quadratic constraint (500 variables): D-Wave never reached the optimum and was about 2× off CPLEX's optimum, but it was faster beyond about 600 variables.
  - Binary quadratic problems, N=500: D-Wave consistently reached the optimum faster than CPLEX, which plateaued at about 25% deviation with a 1000 s cap. **This is the one clear D-Wave win.**
  - MILP unit commitment (a scheduling problem)
    - Full scale (44,544 variables, 42,899 constraints): no feasible solution in 50 default 20 s runs. The best D-Wave cost was 5.7×10⁶ after 400 s, against Gurobi's 1.23×10⁶ at a 0.05% gap in 180 s, about 4.6× worse.
    - Reduced-1 (11,136 variables): the best D-Wave result was about 10× the optimum. Gurobi solved it optimally in 1.34 s.
    - Reduced-2 (1,418 variables): D-Wave came within 2.4% of the optimum in 5 s average, against Gurobi optimal in under 1 s.
  - [arXiv 2409.05542v4](https://arxiv.org/html/2409.05542v4)
- **Abbas et al., "Challenges and Opportunities in Quantum Optimization" (46 authors; Nature Reviews Physics 2024, DOI 10.1038/s42254-024-00770-9)** [opened, abstract]
  - Uses complexity theory to separate provable settings from heuristic ones.
  - Frames advantage as possible only in specific contexts.
  - Proposes clear metrics for fair comparison with classical methods.
  - Does not claim demonstrated optimisation advantage.
  - [arXiv 2312.02279](https://arxiv.org/abs/2312.02279)
- **Quantum-inspired vs quantum on max-cut (Communications Physics 2024)** [search summary]: ballistic simulated bifurcation (classical) had about a 50× time-to-solution advantage over quantum annealing on small graphs. Discrete SB beat D-Wave Advantage on large Pegasus graphs. [Commun. Phys. 2024](https://link.springer.com/article/10.1038/s42005-024-01705-7)
- **Vodeb et al. (arXiv 2412.07460, Dec 2024)** [search summary]
  - Ran 139 max-cut instances of 100–10,000 nodes.
  - Classical simulated annealing reached the global optimum on small instances where the D-Wave QPU fell short.
  - Toshiba SBM (classical hardware) was competitive on large instances.
  - [arXiv 2412.07460](https://arxiv.org/abs/2412.07460v1)

### Inferences
- When classical, quantum-inspired heuristics running on GPUs or FPGAs are benchmarked fairly, they match or beat the quantum annealer. That makes the "quantum" label irrelevant to buyer value.
- Healthcare scheduling is constraint-heavy MILP/CP, the regime where hybrid annealing performed worst in the most careful benchmark.
- **Rating**: maturity of the "quantum optimisation advantage" claim for constrained scheduling: not demonstrated. Recommendation: **skip**.

### Gaps
- I did not open a dedicated QAOA scaling benchmark, for example on LABS or Max-Cut versus Gurobi or branch-and-bound. QAOA evidence here rests on the Abbas review.

## Q3. Quantum machine learning in healthcare: reviews with measured performance vs classical models

### Takeaway
The strongest systematic review (Gupta et al., npj Digital Medicine 2025) screened 4,915 records. Only 16 studies tested QML under realistic conditions (hardware or noisy simulation). It found no consistent trend of empirical quantum utility over classical models, and no known provable advantage for classical healthcare data. This matches Agere's own retirement of the tensor-train classifier. Recommendation: **skip**.

### Cited Findings
- **Gupta et al., "A systematic review of quantum machine learning for digital health" (npj Digital Medicine 2025; literature 2015–2024; PRISMA)** [search summary; the PMC page returned a CAPTCHA and the arXiv HTML was not opened]
  - Funnel: 4,915 records → 169 eligible → 123 excluded for insufficient rigour → 46 → only 16 under realistic QML operating conditions.
  - The review reports no consistent trend supporting empirical quantum utility in digital health.
  - It finds widespread technical misconceptions in the literature.
  - Nearly all models are linear quantum models.
  - Whether any advantage survives noise is largely unaddressed.
  - No theoretically provable advantage exists for classical healthcare datasets.
  - [arXiv 2410.02446](https://arxiv.org/html/2410.02446v2); [PMC12048600](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12048600/)
- **Teixeira and Bianchi review (2018–2025, PRISMA-aligned)** [search summary, excerpts only]: empirical support is limited and mostly restricted to simulators. Classical SVM is the common baseline. [SBC ENIAC](https://sol.sbc.org.br/index.php/eniac/article/download/46839/46596/)

### Inferences
- **Rating**
  - Hospital and health-plan questions covered: readmission and risk scoring, diagnosis classification.
  - Data needed: standard claims or EHR tabular features.
  - Maturity: TRL 2–3.
  - Baseline that it fails to beat: logistic regression and gradient boosting.
  - Recommendation: **skip**. Re-open only if a peer-reviewed study shows a hardware-run QML model beating tuned gradient boosting on a real clinical dataset of realistic size.

### Gaps
- Exact effect sizes (AUC deltas) from individual studies inside the reviews were not extracted, because the full text could not be opened.

## Q4. Tensor networks: where they genuinely beat alternatives, and healthcare tabular evidence

### Takeaway
Tensor networks clearly win at **classical simulation of quantum systems**. A 2024 tensor-network simulation of IBM's 127-qubit "utility" experiment was more accurate than the quantum device in the verifiable regime. Their established roles are simulation and model compression. I found no evidence that tensor-network classifiers beat gradient boosting on tabular or healthcare data. Recommendation: **skip** for prediction, confirming the earlier retirement. **Park** for compression, which is only relevant if Agere ever needs to shrink local model weights, and only against standard quantisation baselines.

### Cited Findings
- **Tindall, Fishman, Stoudenmire, Sels, "Efficient tensor network simulation of IBM's Eagle kicked Ising experiment" (PRX Quantum, Jan 2024)** [search summary]
  - Method: a belief-propagation tensor network matched to the heavy-hex lattice.
  - The authors report results significantly more accurate than the quantum device in the verifiable regime.
  - Separately, Begušić and Chan's sparse Pauli dynamics reproduced the experiment orders of magnitude faster on a single laptop core.
  - [arXiv 2306.14887](https://arxiv.org/abs/2306.14887v1); [APS Physics](https://physics.aps.org/articles/pdf/10.1103/Physics.17.13)
- **Tabular ML benchmarks (TabArena 2025; Shmuel et al. 2024)** [search summary]
  - Gradient-boosted trees remain strong baselines.
  - Deep learning is frequently equivalent or inferior to gradient boosting machines.
  - Foundation models excel on small datasets.
  - Neither benchmark includes tensor-network classifiers.
  - [TabArena arXiv 2506.16791](https://arxiv.org/html/2506.16791v1); [arXiv 2408.14817](https://arxiv.org/pdf/2408.14817v1)

### Inferences
- Tensor networks are not on the main tabular benchmarks, which is itself a signal that they are not competitive there.
- Agere's retired tensor-train classifier result is consistent with the external evidence.
- **Rating**: tensor-train prediction: TRL 4 (it works but brings no advantage), **skip**. Tensor compression of LLM/SLM weights: **park**, because it was not researched here and would need to beat int4/int8 quantisation on accuracy per byte.

### Gaps
- I found no healthcare tabular study using tensor networks with a gradient-boosting baseline.
- Tensor-network LLM compression claims were not researched in this pass.

## Q5. Quantum-inspired algorithms (digital annealers, simulated bifurcation) on classical hardware for scheduling: real hospital deployments with numbers

### Takeaway
There is one real hospital deployment: Fujitsu Digital Annealer for OR block allocation at Baptist Health South Florida, announced 2023. Its only number is a vendor-reported 37% gain in available prime-time minutes over 3 months. No classical baseline is reported, and I found no independent evaluation. Recommendation: **park** the quantum-inspired solvers. The buyer problem (OR block reallocation) is real and is a good **trial** candidate using CP-SAT/MILP with a release-the-unused-block heuristic as the baseline.

### Cited Findings
- **Fujitsu press release, 14 Sep 2023** [opened]
  - "Surgical Capacity Optimization" is a SaaS on the Digital Annealer (described as "quantum-inspired") that recommends OR block-time changes and includes a what-if block-scheduling wizard.
  - It reports a 37% improvement in available prime-time minutes over 3 months.
  - No classical baseline comparison is mentioned.
  - The 14-OR and 231-block figures appear in reprints but not in the release text I read.
  - [PR Newswire](https://prnewswire.com/news-releases/fujitsu-and-baptist-health-south-florida-transform-operating-room-scheduling-with-newly-launched-solution-to-boost-utilization-rates-and-the-financial-health-of-the-surgical-discipline-301927611.html); [HospiMedica](https://www.hospimedica.com/latest-news/articles/294798729/surgical-capacity-optimization-solution-helps-hospitals-boost-or-utilization.html) [search summary]
- **Digital Annealer vs Gurobi on industrial scheduling (Leib et al. 2023)**: "promising results for the digital annealer". This is not a hospital setting. [opened abstract] [arXiv 2309.09736](https://arxiv.org/abs/2309.09736)
- **Simulated bifurcation vs D-Wave** [search summary]: simulated bifurcation beats quantum annealing on max-cut time-to-solution. These studies did not include Gurobi or CP-SAT. [Commun. Phys. 2024](https://link.springer.com/article/10.1038/s42005-024-01705-7)

### Inferences
- The 37% figure most plausibly comes from reallocating under-used blocks. Standard MILP or rule-based block release would likely capture much of it, but that is untested, so this is an inference only.
- The Digital Annealer is a proprietary heuristic. Nothing shows it beating open CP-SAT or HiGHS on constrained hospital instances.
- **Rating**
  - Hospital question: OR block allocation and prime-time utilisation.
  - Data needed: case logs, block schedules, surgeon utilisation, release rules.
  - Maturity: TRL 7–8 as a product, unvalidated against classical solvers.
  - Recommendation: **park** quantum-inspired solvers. **Trial** the buyer problem with a classical solver.

### Gaps
- I found no peer-reviewed or independent evaluation of the Baptist deployment.
- I found no other hospital deployments of simulated bifurcation or digital annealers with numbers.

## Q6. Government, national lab and academic roadmaps for fault-tolerant hardware

### Takeaway
Credible public timelines put useful fault-tolerant machines at about 2029 (IBM Starling, a vendor target) and test whether utility-scale is achievable by 2033 (the DARPA Quantum Benchmarking Initiative). Neither is a guarantee. Even then, the expected early applications are chemistry and materials simulation, not hospital scheduling. Recommendation: **park** the whole area with a re-review trigger tied to independently verified fault-tolerant milestones.

### Cited Findings
- IBM advanced to Stage C of DARPA QBI (reported 7 Oct 2026), where DARPA's independent verification team physically tests the hardware. IBM still targets fault-tolerant Starling in 2029. [search summary] [The Quantum Insider](https://thequantuminsider.com/2026/10/07/ibm-advances-to-stage-c-of-darpa-quantum-benchmarking-initiative/)
- QBI asks whether an industrially useful quantum computer, whose computational value exceeds its cost, can be built by 2033. DARPA reportedly expects someone may get there, but does not know which approach. [search summary] [The Quantum Insider](https://thequantuminsider.com/2026/10/07/ibm-advances-to-stage-c-of-darpa-quantum-benchmarking-initiative/); [Quantum Computing Report](https://quantumcomputingreport.com/ibm-advances-to-stage-c-of-darpa-quantum-benchmarking-initiative-to-evaluate-fault-tolerant-system-hardware/)
- A secondary source puts Starling's targets at 200 logical qubits and 100 million gates. This was not confirmed against IBM primary material. [search summary] [The Quantum Insider](https://thequantuminsider.com/?p=2395144)

### Inferences
- Even 200 logical qubits is far below the QUBO sizes of a realistic hospital roster. Abbas et al. also give no provable speedup for heuristic combinatorial optimisation.
- A reasonable re-review trigger: an independently verified fault-tolerant system, plus a peer-reviewed constrained-scheduling benchmark that beats Gurobi or CP-SAT on wall-clock time and cost at equal quality.

### Gaps
- I did not open NIST, DOE or national-lab roadmap pages directly. NIST post-quantum cryptography is a separate matter, relevant to data security rather than analytics.
- Other hardware makers' roadmaps (Google, Quantinuum, IonQ, QuEra) were not checked.

### Summary table

| Idea | Hospital question | Best evidence | Maturity | Recommendation |
|---|---|---|---|---|
| Pure quantum annealing | Nurse rostering | 3–4 nurses, often infeasible, no baseline | TRL 2–3 | Skip |
| D-Wave hybrid CQM | Rostering, OR, beds | Loses badly to Gurobi on constrained MILP; wins only on unconstrained binary quadratic problems | TRL 4–5 (generic) | Skip |
| QAOA / variational optimisation | Any scheduling | No hospital-scale results; no demonstrated advantage | TRL 2 | Skip |
| Quantum ML | Risk and readmission prediction | 16 realistic studies; no consistent advantage | TRL 2–3 | Skip |
| Tensor-train classifier | Tabular prediction | Absent from tabular benchmarks; Agere retired it | TRL 4 | Skip (confirmed) |
| Tensor networks for compression and simulation | Local model compression | Strong in quantum simulation; compression unassessed | TRL 5–7 | Park |
| Digital annealer / simulated bifurcation | OR block allocation | One vendor-reported deployment, 37% prime-time gain, no baseline | TRL 7–8 product | Park; trial the problem with CP-SAT/MILP |
| Fault-tolerant quantum | Any | Targets 2029 (IBM), 2033 test (DARPA) | n/a | Park; re-review on verified milestones |
