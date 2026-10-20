import React,{useState,useEffect} from 'react';
import {createRoot} from 'react-dom/client';
import {ArrowUpRight} from 'lucide-react';
import './style.css';
import './observatory.css';
import {LivingExperience,HumanFinale} from './LivingExperience.jsx';
import {BuyerSection,BriefingSection,DifferenceSection,NextStep} from './ProductExperience.jsx';
import {LiveQuestion} from './LiveQuestion.jsx';
import {comparisons} from './productDemos.js';
import {demoHref,demoLabel} from './siteContent.js';
import {ProductAdvantageSection} from './ProductAdvantageSection.jsx';
import './motion.css';
import './experience.css';
import './product.css';
import './living.css';
import {ArchitectureSection} from './ArchitectureSection.jsx';
import {PhysicianValueSection} from './PhysicianValueSection.jsx';
import './physician-value.css';
import './design-system.css';
import './architecture-overview.css';
import './wisdom-progress.css';
import './product-advantage.css';
function Logo(){return <a className="logo" href="#top" aria-label="Agere home"><span className="logo-mark"><i/><i/><i/></span>Agere<span className="brand-period">.</span></a>}
function App(){
 const [motion,setMotion]=useState(()=>!window.matchMedia('(prefers-reduced-motion: reduce)').matches),[scrolled,setScrolled]=useState(false),[track,setTrack]=useState('clinical');
 useEffect(()=>{document.documentElement.classList.toggle('motion-paused',!motion);return()=>document.documentElement.classList.remove('motion-paused')},[motion]);
 useEffect(()=>{const f=()=>setScrolled(window.scrollY>60);f();window.addEventListener('scroll',f,{passive:true});return()=>window.removeEventListener('scroll',f)},[]);
 const builtLabel=new Intl.DateTimeFormat('en-US',{dateStyle:'medium',timeStyle:'short',timeZone:'America/Chicago'}).format(new Date(__SITE_BUILT_AT__));
 return <div className={'observatory product-site '+(!motion?'motion-off':'')}>
 <a className="skip-link" href="#ask">Skip to your question</a>
 <header className={'site-header'+(scrolled?' scrolled':'')}><Logo/><nav aria-label="Main navigation"><a href={demoHref}>Ask Agere</a><a href="/ask#saved">Saved analyses</a><a href="/action-center">Follow-up work</a><a href="/learn">Learning</a></nav><a className="nav-cta" href={demoHref}>{demoLabel} <ArrowUpRight size={16}/></a></header>
 <main><div className="site-integration-note"><span>Connected local workspace</span><a href="#ask">Ask a live question ↗</a><small>Care journeys and rewards are illustrative. Synthetic data only.</small></div><LivingExperience track={track} setTrack={setTrack} motion={motion} setMotion={setMotion}/><HumanFinale motion={motion}/><ArchitectureSection motion={motion} setMotion={setMotion}/><ProductAdvantageSection/><PhysicianValueSection motion={motion} onExploreCare={()=>setTrack('clinical')}/><LiveQuestion/><BriefingSection/><BuyerSection/><DifferenceSection comparisons={comparisons}/><NextStep/>
 </main><footer><Logo/><p>Agere · Latin, “to act.”<br/>Connected local workspace · Synthetic data<br/><span>Last updated: <time dateTime={__SITE_BUILT_AT__}>{builtLabel} CT</time></span></p><div><a href={demoHref}>{demoLabel} <ArrowUpRight size={14}/></a><a href="#physician-value">Physician value <ArrowUpRight size={14}/></a><a href="#top">Back to top ↑</a></div></footer>
 </div>
}
createRoot(document.getElementById('root')).render(<App/>);
