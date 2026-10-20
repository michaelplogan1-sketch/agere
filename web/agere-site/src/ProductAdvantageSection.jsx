import React from 'react';
import {ArrowRight,ArrowUpRight,ChevronDown} from 'lucide-react';
import {demoHref,demoLabel} from './siteContent.js';

const cmsPayments='https://www.cms.gov/priorities/innovation/files/access-payments-amts-perf-targets.pdf';
const cmsAco='https://www.cms.gov/priorities/innovation/access-technical-frequently-asked-questions';

export function ProductAdvantageSection(){
 return <section className="product-advantage" id="product-advantage" aria-labelledby="advantage-title">
  <div className="product-section-top"><span className="mono">AGERE / LATIN, “TO ACT”</span><span className="mono">UNDERSTAND → DECIDE → FOLLOW THROUGH</span></div>
  <div className="advantage-intro"><h2 id="advantage-title">The answer is<br/><em>only the beginning.</em></h2><p>Data becomes intelligence. Intelligence becomes knowledge. Wisdom is knowing what to do next—and following through to see what actually happened.</p></div>

  <div className="advantage-grid">
   <article className="advantage-program">
    <span className="advantage-index mono">01 / SIMULATE</span>
    <h3>Test before you fund it.</h3>
    <p>Simulate a program on your organization’s own population before launch. Compare projected admissions and ED visits avoided, net savings and shared-savings impact—with ranges—against doing nothing or an alternative program.</p>
    <div className="program-comparison" aria-label="Compare a program with the baseline and an alternative"><span>Do nothing</span><ArrowRight size={16}/><strong>Your program</strong><ArrowRight size={16}/><span>Alternative</span></div>
    <div className="advantage-output mono">YOUR POPULATION · YOUR ASSUMPTIONS · A RANGE OF OUTCOMES</div>
   </article>
   <article className="advantage-access">
    <span className="advantage-index mono">02 / MODEL THE ECONOMICS</span>
    <h3>CMS ACCESS, built in.</h3>
    <p>Model outcome-aligned payments using CMS’s published amounts and performance rules. For 2026–27, CMS anticipates no ACCESS OAP impact on MSSP and ACO REACH benchmark or performance-year calculations.</p>
    <dl className="access-facts"><div><dt>MSK annual allowed amount</dt><dd>$180 <small>per beneficiary</small></dd></div><div><dt>Outcome attainment threshold</dt><dd>50% <small>meet all required targets</small></dd></div></dl>
    <details className="advantage-detail"><summary>Payment rules & sources <ChevronDown size={14}/></summary><p>The $180 initial-period amount includes Medicare and beneficiary coinsurance. At least half of aligned beneficiaries must meet all required targets for full payment under the clinical outcome adjustment; substitute-spend and other payment rules also apply.</p><p>For 2026–27, CMS anticipates no ACCESS OAP impact on MSSP and ACO REACH benchmark or performance-year calculations. These expenditures enter the calculations from 2028.</p><a href={cmsPayments} target="_blank" rel="noreferrer">CMS amounts & performance targets <ArrowUpRight size={13}/></a><a href={cmsAco} target="_blank" rel="noreferrer">CMS technical FAQ <ArrowUpRight size={13}/></a></details>
   </article>
   <article>
    <span className="advantage-index mono">03 / COMPILE WHAT YOU KNOW</span>
    <h3>The cheapest correct path.</h3>
    <p>Common questions run through compiled, verified logic in seconds with no model call. Frontier reasoning handles the parts that need it. Accepted logic is reused against the data.</p>
    <div className="route-measure"><strong>4 of 5</strong><span>repeat answers<br/><b>~1–4 seconds · $0 model cost</b></span></div>
    <p className="advantage-context">Measured on the five-question repeat set, in each of two saved runs.</p>
   </article>
   <article>
    <span className="advantage-index mono">04 / ASK FOR THE DELIVERABLE</span>
    <h3>Ask for anything.</h3>
    <p>Ask for a simulator, a negotiation brief or a board story. A frontier model designs the deliverable; quantitative results come from the verified stack, with scenario assumptions kept explicit.</p>
    <blockquote>“Build a negotiation simulator for our MA contract.”</blockquote>
    <blockquote>“A board story of our ACCESS MSK launch.”</blockquote>
   </article>
   <article>
    <span className="advantage-index mono">05 / READ THE CLINICAL CONTEXT</span>
    <h3>Read beyond the rows.</h3>
    <p>Understand why a therapy is held, not just whether it is missing. Small local classifiers and typed deciders read free-text notes; the document reader also accepts faxes and PDFs. Routine extraction can cost fractions of a cent, with larger models reserved for unresolved context.</p>
    <blockquote>“Why isn’t this patient on an MRA?”</blockquote>
    <div className="advantage-tags"><span>Intolerance</span><span>Potassium</span><span>Kidney function</span><span>Cost</span></div>
   </article>
   <article>
    <span className="advantage-index mono">06 / TEST AGAINST AGREED ANSWERS</span>
    <h3>Correctness you can check.</h3>
    <p>Nine real population-health questions are locked with an independent party. On the five-question repeat set, Agere returned the exact agreed answers on both runs.</p>
    <div className="benchmark-comparison"><div><span>Agere · both repeat runs</span><strong>5 / 5</strong></div><div><span>Model-only baseline · same five</span><strong>1 / 5</strong></div></div>
    <p className="advantage-context">Saved synthetic-pilot comparison · October 18, 2026. The repeat result covers five of the nine locked questions. <a href="/site/evidence/product-summary-2026-10-18.json" target="_blank" rel="noreferrer">Read the aggregate evidence <ArrowUpRight size={11}/></a></p>
   </article>
  </div>
  <div className="advantage-foot"><p>From native files to a verified answer.<br/><strong>From an answer to action that gets checked.</strong></p><a href={demoHref}>{demoLabel} <ArrowUpRight size={17}/></a></div>
 </section>
}
