export function navigateTabs(event) {
  const keys=['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'];
  if(!keys.includes(event.key)) return;
  const tabs=Array.from(event.currentTarget.querySelectorAll('[role="tab"]'));
  const current=tabs.indexOf(event.target);
  if(current<0) return;
  event.preventDefault();
  const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(current+(event.key==='ArrowLeft'||event.key==='ArrowUp'?-1:1)+tabs.length)%tabs.length;
  tabs[next].focus(); tabs[next].click();
}
