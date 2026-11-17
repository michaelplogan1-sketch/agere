# Order books and market microstructure methods: what transfers to hospital and health-plan operations analytics

Research date: 2026-11-17. Scope: order-book models (queue-reactive, birth-death queue models, Hawkes processes), queue position and fill probability, market making and inventory control, high-frequency signal detection compared with CUSUM/EWMA, and auction or matching markets with healthcare transfer.

Source handling notes:
- "Opened" means the page itself was fetched and read. "Search snippet" means only the search-engine summary was seen. The report writer should weight opened sources more heavily.
- One Columbia-hosted PDF (Green, Savin and Savva on nurse staffing) was returned as binary by the fetch tool, and its text was not readable. It is cited from the search snippet only and was not used beyond that.
- INFORMS (pubsonline) returned HTTP 403, so the Integrated Block Sharing abstract is cited from the search snippet only.

Summary recommendation table (for the report writer):

| Idea | Healthcare question | Evidence vs simple baseline | Maturity | Recommendation |
|---|---|---|---|---|
| Queue-reactive LOB model (Huang-Lehalle-Rosenbaum 2015) | None directly; at best "state-dependent arrival/departure rates for bed or OR queues" | None in healthcare; no healthcare use found | Finance only | Skip (state-dependent birth-death queues are already standard queueing; Erlang/simulation already present) |
| Cont-Stoikov-Talreja birth-death order-book queues | Same as above | None in healthcare found | Finance only | Skip |
| Hawkes / self-exciting point processes for arrivals | ED arrivals, readmission or next-visit timing, infection clustering (C. difficile), crisis episodes | Where measured against baselines, pure Hawkes lost to linear regression and an RNN on visit timing; only slightly beat homogeneous Poisson | Research prototypes, retrospective | Park (trial only as a cheap overdispersion test inside an existing forecaster benchmark, not as a product feature) |
| Hawkes-driven queues (Daw-Pender) | Staffing under bursty arrivals | Theory only; no healthcare data | Theory | Park |
| Queue position / fill probability | Bed assignment priority, OR block release, transfer centre | No healthcare microstructure-style work found; block-release prediction exists in vendor tools without published baselines | Analogy only | Skip as microstructure; the underlying question (probability an OR block or bed request is filled by time t) is a survival/classification problem |
| Avellaneda-Stoikov market making / inventory | Float pool, bed or staffing buffers, locum purchasing | No healthcare application found; the closest real work is newsvendor staffing models | Analogy only | Skip AS; consider newsvendor-type staffing as a separate, established OR item |
| Order-flow imbalance / HF signal detection | Census surges, early warning | No healthcare evidence; CUSUM/EWMA comparisons exist for Poisson health counts | CUSUM/EWMA mature | Skip microstructure signals; Agere already has Bayesian Blocks; add CUSUM/EWMA as the baseline if not present |
| Kidney exchange (matching market design) | Plan/transplant-centre question, not general operations | Real deployment, chains averaging almost five transplants; but gains largely simulation-based and market fragmented | Deployed nationally | Skip for Agere (not a hospital/plan analytics buyer problem Agere serves) |
| Residency match (deferred acceptance) | Not an Agere buyer problem | Deployed (no new evidence gathered this session) | Deployed | Skip |
| OR time auctions / block marketplaces | OR utilisation, block release | Academic auction designs; vendor "marketplaces" with no published controlled results; no real hospital auction found | Research / vendor claims | Park |
| Nurse shift bidding / dynamic shift pricing | Fill rate, premium labour cost | Vendor case studies only, conflicting numbers, no controlled comparisons | Vendor deployed, unvalidated | Park (could become a descriptive recipe on fill rate and premium pay if a buyer has the data) |

## Limit-order-book models (queue-reactive, Cont-Stoikov-Talreja, Hawkes): any healthcare application, and Hawkes gains vs Poisson/NHPP?

### Takeaway
No application of the queue-reactive model or the Cont-Stoikov-Talreja birth-death order-book model to any hospital or health-plan queue was found. Hawkes processes have been applied to healthcare event data (readmission and next-visit timing, C. difficile spread, sickle cell pain crises, admission histories), but the one study with a numeric baseline comparison shows a pure Hawkes model losing badly to linear regression and an RNN on visit-timing error, and only modestly beating a homogeneous Poisson process. No study comparing Hawkes against Poisson/NHPP for ED arrival counts was found.

### Cited Findings
- Queue-reactive model (Huang, Lehalle, Rosenbaum; JASA 2015, arXiv 2013). It splits time into periods where the reference price is constant, and within each period treats the order book as a Markov queueing system whose order-flow intensities depend only on the current book state. It is fit to ultra-high-frequency order-book data. Its stated uses are market simulation and transaction-cost analysis of trading algorithms. The abstract reports no baseline comparison. Opened. — [arXiv 1312.0563](https://arxiv.org/abs/1312.0563)
- A search for applications of the queue-reactive model outside finance returned only market-microstructure follow-ups (ergodicity results, a Tokyo tick-size study, a deep-learning queue-reactive simulator, arXiv 2603.24137). Search snippet. — [econpapers listing](https://econpapers.repec.org/paper/arxpapers/1312.0563.htm)
- MEDCAS (arXiv preprint, Feb 2021, no venue stated on the page). It predicts next-visit timing and diagnoses from EHR visit sequences on heart failure (10,713 patients, 204,753 visits), chronic liver disease (3,830 patients, 122,733 visits) and MIMIC (7,499 patients, 19,911 visits). Opened. — [ar5iv 2102.02586](https://ar5iv.labs.arxiv.org/html/2102.02586)
  - Time-prediction RMSE of log time (lower is better), HF / CL / MIMIC: MEDCAS 1.709 / 1.695 / 1.395; DoctorAI (RNN) 1.831 / 1.905 / 1.627; best linear regression 2.003 / 1.858 / 1.396; pure Hawkes 3.241 / 3.561 / 4.471; homogeneous Poisson 3.592 / 3.528 / 4.973. — [ar5iv 2102.02586](https://ar5iv.labs.arxiv.org/html/2102.02586)
  - The paper argues that a Hawkes process can capture cascades between visits but relies on a strong prior assumption about how events are generated. — [ar5iv 2102.02586](https://ar5iv.labs.arxiv.org/html/2102.02586)
- Kumar (MIT M.Eng. thesis, May 2020, advisor Guttag) applies multi-task, multi-dimensional Hawkes processes to C. difficile infection across units of a large urban hospital. It learns per-unit influence patterns and clusters among them, proposed to guide unit-specific interventions. The abstract reports no metrics or baseline comparisons. Opened. — [MIT DSpace 1721.1/127420](https://dspace.mit.edu/handle/1721.1/127420)
- Registered point processes (arXiv 1710.01410) learns an 8-dimensional Hawkes process from 1,129 MIMIC patients' admission records and reports strong self-triggering for hypertensive disease. Search snippet. — [arXiv 1710.01410](https://arxiv.org/pdf/1710.01410)
- A 2026 arXiv paper models sickle cell pain crises as self-exciting with an exponential kernel, and addresses processes that are observed only after onset. Search snippet. — [arXiv 2601.04538](https://arxiv.org/html/2601.04538v1)
- Daw and Pender (arXiv 1707.05143) derive exact transient and steady-state moments for infinite-server queues with Hawkes arrivals. Their applications are internet traffic and a nightclub admission-control problem. They report no healthcare or staffing application and no Poisson comparison in the abstract. Opened. — [arXiv 1707.05143](https://arxiv.org/abs/1707.05143)
- A review cautions that when a self-exciting model is fit to infection data, the background term absorbs cases from unobserved infections. Better case reporting then shrinks the apparent background effect, so the parameters are hard to interpret. Search snippet. — [arXiv 1708.02647](https://arxiv.org/pdf/1708.02647)
- A 2024 ED hourly-arrival benchmark (Rostami-Tabar, Browell, Svetunkov, Health Systems 13(2):133-149; one hospital in Wales) compared GAMLSS, ETS/ADAM, Poisson regression, TBATS, Prophet, GBM and an empirical distribution. It did not include Hawkes. GAMLSS with truncated Normal and ADAM did best, and count-data models did worse than continuous ones. TBATS, Prophet and GBM performed poorly. A full ADAM fit took about 1 h 25 min. No numbers were in the opened page. Opened. — [R-bloggers author story](https://www.r-bloggers.com/2023/05/story-of-probabilistic-forecasting-of-hourly-emergency-department-arrivals/); citation details from [Lancaster eprints](https://eprints.lancs.ac.uk/id/eprint/204585) (search snippet)
- A 2019 ED study found that adding prehospital notification time to a Poisson time-series model substantially improved accuracy, especially at a one-hour horizon. Search snippet. — [PMC6683581](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6683581/)
- Toronto EMS call counts (Annals of Applied Statistics, 2011) were modelled with integer time series and a dynamic latent factor structure, which significantly reduced forecast error. This is not Hawkes. Search snippet. — [arXiv 1107.4919](https://arxiv.org/pdf/1107.4919)

### Inferences
- The microstructure models are state-dependent birth-death queues tuned to tick-level price formation. A bed or OR queue has no price, no cancellation-versus-execution race and no adversarial participants, so the transferable part is already ordinary queueing (state-dependent rates), which Agere covers with Erlang models and simulation. Transfer is analogy only.
- For Hawkes, the measured evidence (MEDCAS) says self-excitation alone is a weak model for healthcare event timing: it lost to linear regression on every dataset. Agere's foundation forecasters (Chronos-2, TimesFM) and conformal intervals are better candidates for arrival forecasting. The bar a Hawkes trial would need to clear is GAMLSS/ETS-class benchmarks, not Poisson.
- A Hawkes model may still add something narrow: a test for clustering or overdispersion in event streams such as infection onsets, falls or readmission bursts on a unit. That is a quality-buyer question ("is this unit's cluster beyond background?"). It is unproven, though; the C. difficile work reports no detection metrics.

### Gaps
- No published comparison of Hawkes against Poisson/NHPP for ED arrivals, bed requests, OR scheduling, claims queues, prior-authorisation queues, call centres or staffing was found.
- No real-time outbreak-alarm validation of Hawkes against known hospital outbreaks or against standard tools (CUSUM, SaTScan) was found.
- Cont-Stoikov-Talreja (Operations Research 2010) was not opened in this session; no healthcare use was surfaced.
- Numeric results for the 2024 ED benchmark were not available on opened pages.

## Queue position and fill probability analogues (bed assignment, OR block release, transfer centre queues)

### Takeaway
No healthcare work framing bed assignment, OR block release or transfer-centre queues as queue-position or fill-probability problems in the microstructure sense was found. Block release is handled by fixed-lead-time release rules and vendor tools that predict unfilled blocks, with no published controlled comparisons. Integrated block sharing and an academic combinatorial auction exist as OR research.

### Cited Findings
- Hospitals often release unused OR blocks automatically at a fixed lead time, anywhere from the morning of surgery to two weeks ahead. Search snippet. — [Vizient](https://newsroom.vizientinc.com/evaluating-your-block-schedule-dive-right-in.htm)
- Vendor tools use machine learning to predict which blocks will go unfilled and to request their release. One vendor offers a "block marketplace" that nudges earlier release. These are vendor descriptions, without published baselines. Search snippet. — [HISTalk](https://histalk2.com/?p=93332); [vendor case page](https://caresyntax.com/?p=36162)
- Integrated Block Sharing (M&SOM, 2012) pools blocks across surgeons to improve access and utilisation. The page returned 403, so this is from the search snippet. — [INFORMS M&SOM 10.1287/msom.1110.0372](https://pubsonline.informs.org/doi/10.1287/msom.1110.0372)

### Inferences
- "Probability that a bed request or released block is filled by time t" is a time-to-event or classification task. The fitting baselines are a historical fill-rate table by service, weekday and lead time, plus logistic or survival regression, not microstructure fill-probability models. Agere's classifier and saved SQL recipes can answer this for an operations or perioperative buyer without new mathematics.
- Treat it as analogy only. The useful idea from microstructure is "fill probability depends on queue position and lead time," which perioperative analytics already uses in block-release timing.

### Gaps
- No peer-reviewed study measuring block-release prediction against a fixed-release rule was found in this session.
- No transfer-centre queue analytics study was found.

## Market making / inventory control (Avellaneda-Stoikov) analogues: bed or staffing buffers, float pool, locum purchasing

### Takeaway
No healthcare application of Avellaneda-Stoikov or other market-making inventory models was found. The healthcare analogue with real research behind it is the newsvendor staffing model, plus vendor-reported results for float pools and dynamic shift pricing. Those vendor results are uncontrolled and inconsistent.

### Cited Findings
- Green, Savin and Savva use a newsvendor variant to set nurse staffing levels from ED data at a large urban hospital, and find that absenteeism correlates with anticipated workload. Search snippet only; the PDF was unreadable and not verified. — [Columbia Business School hosted paper](https://business.columbia.edu/sites/default/files-efs/pubfiles/4866/GreenSavinSavvaPaper.pdf)
- A 2025 University of Arkansas capstone applied a newsvendor model to float-pool design across single and multi-hospital pools. It is a student project and not an outcome study. Search snippet. — [UArk capstone](https://engineering.uark.edu/_resources/images/industrial/ineg-capstone-2025-baptist.pdf)
- A vendor claims a health system cut travellers by 67% (about $10M over 18 months) after expanding its float pool. This is promotional and uncontrolled. Search snippet. — [HIT Consultant](https://hitconsultant.net/2024/03/27/is-float-pool-nursing-the-way-out-of-the-staffing-shortage)

### Inferences
- Avellaneda-Stoikov optimises bid/ask quotes under inventory risk with a continuous price process and Poisson fills. A hospital buys premium labour, with no two-sided quoting. The defensible core is "hold a buffer sized to the cost of shortage versus surplus," which is the newsvendor critical-fractile rule. Transfer from AS is analogy only.
- For a CFO or nursing-operations buyer, the item that would pass the owner's bar is a newsvendor or critical-fractile float-pool and premium-labour sizing recipe. It would take Agere's probabilistic census or forecast quantiles (already present via conformal intervals and Chronos-2/TimesFM) and compare against current fixed-ratio staffing on historical cost and shortfall. That is an established OR method, not microstructure.

### Gaps
- No controlled study of newsvendor-based float-pool policies against cost or patient outcomes was found.
- No locum-purchasing optimisation studies were found.

## High-frequency signal detection, order-flow imbalance, change-point detection: what is proven in hospital census / early warning vs CUSUM/EWMA?

### Takeaway
No healthcare evidence for order-flow-imbalance-style signals was found. For Poisson health counts, CUSUM and EWMA each win in different regimes: CUSUM is better for large, late shifts and EWMA for small, early shifts. No study of daily hospital census specifically was found, and census series are autocorrelated and seasonal, which breaks the basic chart assumptions.

### Cited Findings
- On Poisson health counts, CUSUM charts were better for large shifts occurring later, and EWMA charts were better for small shifts occurring early, compared with temporal scan statistics. Search snippet. — [Korea University record](https://pure.korea.ac.kr/en/publications/a-comparison-of-cusum-ewma-and-temporal-scan-statistics-for-detec/)
- A multi-CUSUM chart detected rate shifts in disease incidence faster than EWMA and EWMA-CUSUM multi-charts. Search snippet. — [PMC7245694](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7245694/)
- A CDC study used CUSUM change-point detection on influenza-like-illness ED visits and found it robust to autocorrelation. Search snippet. — [CDC stacks](https://stacks.cdc.gov/view/cdc/21324/cdc_21324_DS1.pdf)

### Inferences
- Order-flow imbalance is a signed buy-minus-sell pressure measure with no healthcare equivalent beyond "admissions minus discharges," which is census change itself. The analogy is trivial and adds nothing beyond the net-flow metrics Agere can already compute in SQL.
- For census or early-warning alarms, the simple baseline that must be beaten is CUSUM or EWMA on residuals from a seasonal forecast. If Agere's Bayesian Blocks component lacks a head-to-head comparison against CUSUM and EWMA on residuals, that comparison is the qualification step the owner's rule requires.

### Gaps
- No study of change-point detection on hospital census or occupancy with measured detection delay and false-alarm rate was found in this session.
- Patient-level early-warning scores were out of scope and not searched.

## Auction / matching market design with proven healthcare transfer (kidney exchange, residency match, OR time markets, nurse shift bidding)

### Takeaway
Kidney exchange is the clearest proven transfer of market design to healthcare. It is deployed, with chains averaging almost five transplants each, though the efficiency gains are mostly simulation-backed and the US market is fragmented. OR-time auctions exist only as academic designs, with no real hospital auction found. Nurse shift bidding and dynamic pricing have only vendor-reported, conflicting results.

### Cited Findings
- Roth, Sönmez and Ünver's early kidney exchange work (NBER w10002, 2003-2004) used simulations to project more feasible live-donor transplants. Search snippet. — [NBER w10002](https://nber.org/papers/w10002)
- Roth (AEA P&P 2012) reports that non-simultaneous chains became important because of highly sensitised patients and averaged almost five transplants each at large networks. Search snippet. — [Stanford-hosted AEA 2012 paper](https://stanford.edu/%7Ealroth/papers/KidneyExchange%20AEAPP2012.pdf)
- An empirical study found the US kidney exchange market fragmented: 62% of transplants involved donors and patients from the same hospital, and within-hospital matching of easy-to-match pairs is inefficient under theory. Search snippet. — [Agarwal/Azevedo et al., NSF PAR](https://par.nsf.gov/servlets/purl/10188997)
- A 2019 Journal of Combinatorial Optimization paper proposes an iterative combinatorial auction for surgical scheduling that handles surgeons' private availability and room eligibility. It is academic only. Search snippet. — [IDEAS/RePEc](https://ideas.repec.org/a/spr/jcomop/v37y2019i1d10.1007_s10878-017-0247-5.html)
- Shift bidding and dynamic pricing figures are vendor-reported and conflict across sources:
  - One health system's fill rate is reported as 83% to 86% in one source and as 94% in another.
  - One vendor says dynamically priced shifts had a 23% higher fill rate.
  - Another system reports avoiding over $500K through dynamic pricing.
  - None is a controlled comparison. Search snippets. — [Healthcare IT News](https://healthcareitnews.com/news/mercy-saves-30-million-2023-ai-powered-nursing-workforce-management-tech); [PR Newswire](https://prnewswire.com/news-releases/carerevs-ai-powered-smart-rates-enables-hospitals-to-fill-shifts-at-a-higher-rate-302150594.html); [vendor case study](https://works.trustedhealth.com/case-study/southern-illinois-healthcare-case-study); [American Nurse](https://www.myamericannurse.com/staffing-though-web-based-open-shift-bidding/)

### Inferences
- Kidney exchange and the residency match are real healthcare market-design successes, but they are clearinghouse mechanisms run by national bodies. They do not answer the CFO, quality, operations or analytics questions Agere's buyers bring. Recommend skipping both as product items; they can be cited as proof that market design transfers when there is a matching problem with incompatible pairs and stable preferences.
- For OR time and shift markets, Agere's defensible role is descriptive measurement rather than running a market: block-release lead time versus fill, and open-shift fill rate versus premium-pay uplift. Both are SQL recipes over scheduling data, and an LLM alone cannot produce them without the data. Building an auction mechanism would be a new service, which falls outside the current boundaries.
- Overall: the transfer from order-book and microstructure methods to hospital and plan operations is analogy only in every area examined, except Hawkes processes. Hawkes has real but weak healthcare evidence, and where it was tested numerically it underperformed simple regression.

### Gaps
- No published evaluation of a deployed OR time auction or trading market with measured utilisation gains was found.
- No peer-reviewed controlled study of nurse shift bidding or dynamic shift pricing was found.
- Residency match (NRMP, Roth-Peranson 1999) was not re-verified in this session; it predates the 2015-2026 window.
