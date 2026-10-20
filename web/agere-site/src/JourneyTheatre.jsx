import React,{useEffect,useRef} from 'react';

function SimpleCube(){
 return <svg className="simple-cube" viewBox="0 0 100 112" aria-hidden="true" focusable="false">
  <path fill="currentColor" d="M50 4 96 30V82L50 108 4 82V30Z"/>
  <path fill="#fff" fillOpacity=".18" d="M50 4 96 30 50 56 4 30Z"/>
  <path fill="#000" fillOpacity=".18" d="M50 56 96 30V82L50 108Z"/>
 </svg>
}

const TAU=Math.PI*2, COUNT=32;
const clamp=x=>Math.max(0,Math.min(1,x));
const smooth=x=>{x=clamp(x);return x*x*(3-2*x)};
const mix=(a,b,t)=>a+(b-a)*t;
const clinicalLabels=[
 ['PATIENT CONTEXT','ECHO','MEDICATIONS','LABS'],
 ['ONE PATIENT','EJECTION FRACTION','CLINICAL HISTORY','MEDICATION LIST'],
 ['ARNI / ACEi / ARB','BETA BLOCKER','MRA','SGLT2i'],
 ['PATIENT','PHARMD PROGRAM','COVERAGE','CARE TEAM'],
 ['OUTREACH','CONSENT','ENROLLMENT','VISIT BOOKED'],
 ['REVIEW RESPONSE','PHARMD APPROVAL','DOSE STEP','FOLLOW-UP'],
 ['CARE TEAM','BLOOD PRESSURE','HOME LABS','SYMPTOMS'],
 ['MORE DAYS AT HOME','SYMPTOMS','TREATMENT','FOLLOW-THROUGH'],
 ['VALUE CREATED','CARE PAYMENTS','QUALITY REWARDS','SHARED SAVINGS']
];
const financialLabels=[
 ['FINANCIAL CONTEXT','CLAIMS','PAYMENTS','CONTRACTS'],
 ['CONNECTED RECORD','SERVICE DATE','ATTRIBUTION','PAYMENT'],
 ['SOURCE TOTAL','CLAIMS','PAYMENTS','RECONCILIATION'],
 ['SETTLEMENT','BENCHMARK','SHARING TERMS','COST PATTERNS'],
 ['EARNED SAVINGS','PRACTICE A','PRACTICE B','PRACTICE C'],
 ['NEXT DECISION','OPPORTUNITY','CONTEXT','ACTION']
];
// Four focal objects have distinct roles. Small records carry the motion between them.
function moduleAt(stage,i){
 const layouts=[
  [[53,49,0,0],[19,26,.58,.5],[83,24,.48,.4],[82,78,.68,.6]],
  [[50,49,1.9,1],[18,25,.72,.8],[83,27,.72,.8],[80,78,.72,.8]],
  [[27,30,1.14,1],[73,30,1.14,1],[27,76,1.14,1],[73,76,1.14,1]],
  [[25,48,1.45,1],[73,48,1.45,1],[51,16,.48,.6],[51,84,.48,.6]],
  [[18,48,.85,1],[40,48,.85,1],[62,48,.85,1],[84,48,.85,1]],
  [[30,27,1.06,1],[73,29,1.06,1],[72,76,1.06,1],[28,75,1.06,1]],
  [[50,48,1.7,1],[16,28,.68,1],[84,28,.68,1],[83,78,.68,1]],
  [[50,55,1.25,1],[13,26,.42,0],[88,30,.42,0],[83,85,.42,0]],
  [[24,49,1.65,1],[77,19,.82,1],[77,49,.82,1],[77,79,.82,1]]
 ];return (layouts[Math.min(stage,8)]||layouts[0])[i];
}
function chipAt(stage,i){
 const lane=i%4,j=Math.floor(i/4),a=i/COUNT*TAU;
 if(stage===0){const angle=i*2.3999632297,r=43*Math.sqrt((i+.5)/COUNT);return [50+Math.cos(angle)*r,49+Math.sin(angle)*r*.98,.6+(i%4)*.12,(i%7-3)*9];}
 if(stage===1){const a=j/8*TAU;return [50+Math.cos(a)*(31+lane*2),49+Math.sin(a)*(27+lane*2),.7,0]}
 if(stage===2){const m=moduleAt(stage,lane);return [m[0]-10+(j%4)*6.5,m[1]+12+Math.floor(j/4)*5,.65,0]}
 if(stage===3){return [33+j*4.7,40+lane*5,.72,0]}
 if(stage===4){return [12+j*11,66+lane*5,.65,0]}
 if(stage===5){return [50+Math.cos(a)*34,51+Math.sin(a)*34,.8,a*180/Math.PI+90]}
 if(stage===6){return [18+j*9,57+(lane-1.5)*6+Math.sin(j*.9+lane)*3,.65,0]}
 if(stage===7){
  const points=[];for(let j=0;j<9;j++)points.push([15+j*8.75,39-Math.min(j,8-j)*7]);
  for(let j=1;j<=5;j++){points.push([15,39+j*9]);points.push([85,39+j*9])}
  for(let j=0;j<9;j++)points.push([15+j*8.75,89]);
  points.push([42,77],[42,66],[58,66],[58,77]);return [...points[i],1.12,0];
 }
 return [34+j*5,25+lane*17,.7,0]
}
const linePaths=[
 'M58 80H145L190 160 M450 74L415 110 M490 330H445L420 290',
 'M120 120L300 235L492 135 M300 235L480 360',
 'M162 145H438V365H162Z M300 145V365 M162 255H438',
 'M150 235H438 M300 80V400',
 'M108 235H504',
 'M180 130H438V365H168V130Z',
 'M96 135L300 230L504 135 M300 230L498 375',
 'M90 187L300 53L510 187V427H90Z M252 427V317H348V427',
 'M145 235H310V90H462 M310 235H462 M310 235V380H462'
];
export default function JourneyTheatre({stage=0,position=0,motion=true,track='clinical',finale=false}){
 const root=useRef(),modules=useRef([]),chips=useRef([]),labels=useRef([]),input=useRef({position,stage,motion}),pointer=useRef({x:0,y:0});
 const sceneStage=track==='financial'?[0,1,2,3,8,6][stage]:stage;
 input.current={position,stage:sceneStage,motion};
 useEffect(()=>{
  let frame,last=0,t=0,current=sceneStage,visible=true;const observer=new IntersectionObserver(([e])=>{visible=e.isIntersecting;if(visible&&!frame)frame=requestAnimationFrame(draw)},{rootMargin:'120px'});observer.observe(root.current);
  function draw(now){frame=0;if(!visible)return;const dt=Math.min((now-last)/1000||.016,.05);last=now;const v=input.current;
   if(v.motion)t+=dt;
   let target=track==='clinical'?v.position:v.stage;
   if(finale)target=7;
   current=v.motion?mix(current,target,1-Math.exp(-dt*9)):v.stage;
   const a=Math.floor(current),b=Math.min(8,a+1),blend=smooth(current-a);
   modules.current.forEach((el,i)=>{if(!el)return;const from=moduleAt(a,i),to=moduleAt(b,i),x=mix(from[0],to[0],blend),y=mix(from[1],to[1],blend),s=mix(from[2],to[2],blend),o=mix(from[3],to[3],blend);const drift=v.motion?Math.sin(t*.65+i*1.7)*(i===0?.75:1.1):0;
    el.style.left=`${x+pointer.current.x*(i===0?1.7:.8)}%`;el.style.top=`${y+drift+pointer.current.y*.65}%`;el.style.transform=`translate(-50%,-50%) scale(${s})`;el.style.opacity=o;
    const label=labels.current[i];if(label){label.style.left=`${x}%`;label.style.top=`${y+s*10.4+3}%`;label.style.opacity=(v.stage===0||v.stage===7)?0:o;}
   });
   chips.current.forEach((el,i)=>{if(!el)return;const f=chipAt(a,i),n=chipAt(b,i);let x=mix(f[0],n[0],blend),y=mix(f[1],n[1],blend),s=mix(f[2],n[2],blend),r=mix(f[3],n[3],blend),o=.6;
    if(v.motion){const life=(t*.052+i/COUNT)%1,birth=smooth(life/.12),retire=1-smooth((life-.88)/.12);s*=birth*retire;o=.28+.55*birth*retire;
      if(a===0){x+=Math.sin(t*.6+i*2.7)*4;y+=Math.cos(t*.43+i*.9)*5;r+=Math.sin(t*.5+i)*16;}
      else {x-=(1-birth)*14;y+=(1-birth)*(i%2?12:-12);}
    }el.style.left=`${x}%`;el.style.top=`${y}%`;el.style.transform=`translate(-50%,-50%) rotate(${r}deg) scale(${s})`;el.style.opacity=o;
   });
   if(v.motion)frame=requestAnimationFrame(draw);
  }frame=requestAnimationFrame(draw);return()=>{cancelAnimationFrame(frame);observer.disconnect()}
 },[track,finale,motion,motion?null:stage]);
 const displayStage=finale?7:sceneStage,words=(track==='clinical'?clinicalLabels:financialLabels)[stage]||clinicalLabels[displayStage];
 return <div ref={root} className={'journey-theatre '+(motion?'theatre-running':'')+(finale?' theatre-finale':'')} data-scene={displayStage} role="img" aria-label={finale?'Connected care supports more days at home':`${words[0].toLowerCase()}: data blocks reorganize with the care journey`} onPointerMove={e=>{if(!motion)return;const r=e.currentTarget.getBoundingClientRect();pointer.current={x:(e.clientX-r.left)/r.width*2-1,y:(e.clientY-r.top)/r.height*2-1}}} onPointerLeave={()=>pointer.current={x:0,y:0}}>
  <div className="theatre-grid"/><div className="theatre-halo"/>
  <div className="journey-coordinate mono">AG / {String(displayStage+1).padStart(2,'0')}<span>INTELLIGENCE IN MOTION</span></div>
  <div className="theatre-stage">
   <svg className="theatre-circuit" viewBox="0 0 600 480" preserveAspectRatio="none" aria-hidden="true"><path d={linePaths[displayStage]}/><path className="circuit-signal" d={linePaths[displayStage]}/><circle cx="300" cy="240" r="184" className="theatre-orbit"/><circle cx="300" cy="240" r="196" className="theatre-orbit outer"/></svg>
   {Array.from({length:COUNT},(_,i)=><div className="record-chip" data-block-design="simple" ref={el=>chips.current[i]=el} key={i} aria-hidden="true"><SimpleCube/></div>)}
   {[0,1,2,3].map(i=><React.Fragment key={i}><div className={'theatre-module module-'+i} data-block-design="simple" ref={el=>modules.current[i]=el} aria-hidden="true"><SimpleCube/></div><span className="module-label mono" ref={el=>labels.current[i]=el} aria-hidden="true">{words[i]}</span></React.Fragment>)}
   {displayStage===0&&<><span className="source-tag source-a mono">01 / UNSTRUCTURED NOTES</span><span className="source-tag source-b mono">02 / MEDICATION HISTORY</span><span className="source-tag source-c mono">03 / SIGNALS FROM HOME</span></>}
   {displayStage===2&&<span className="scene-center-tag mono">THERAPY + DOSE + TOLERANCE</span>}
   {displayStage===5&&<span className="scene-center-tag mono">REVIEW → ADJUST → FOLLOW UP</span>}
   {displayStage===7&&<span className="scene-life-label">More life.<em>At home.</em></span>}
  </div>
  <div className="theatre-bottom-line mono"><span><i/> {displayStage===0?'FRAGMENTS BECOME CONTEXT':'CONTEXT BECOMES ACTION'}</span><span>SCROLL TO TRANSFORM ↓</span></div>
 </div>
}
