'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),requests=[],focused=[];
const drawing=new Proxy({}, {get:(o,k)=>o[k]??(()=>{}),set:(o,k,v)=>(o[k]=v,true)});
function el(id){if(!elements.has(id))elements.set(id,{id:id.slice(1),textContent:'',innerHTML:'',value:'A',disabled:false,style:{},classList:{toggle(){}},getContext:()=>drawing,getBoundingClientRect:()=>({width:800,left:0}),setAttribute(){},removeAttribute(){},scrollIntoView(){this.scrolled=true},addEventListener(){}});return elements.get(id)}
const series={available:true,count:10,mean:90,std:1,minimum:89,maximum:91,points:[[0.1,90,89,91],[0.9,90,89,91]],note:'measured'};
const data={scope:'Whole recording',phase:{...series,slope:.1,net_change:.2},z:{A:{...series,reference_degrees:30},B:{available:false,note:'Needs B'}},speed:{...series,variation_percent:2,acceleration:series},widths:{A:{high:series,low:series}},quadrature:{available:true,simultaneous:1,valid_transitions:100,direction_changes:2},differential:{'A/-A':{common_mode_mean:2.5,common_mode_minimum:2.4,common_mode_maximum:2.6,differential_low:-5,differential_high:5,level_imbalance_percent:0,noncomplementary_percent:.01,edge_skew:series,unmatched_edges:0,note:'pair'}},noise:{A:{low_rms:.02,high_rms:.03,raw_transitions:102,short_pulses:1,low_width_limit:.001,high_width_limit:.001}}};
const context={$:el,summary:{start:0,end:1,axis_unit:'s',advanced_analysis:data},window:{devicePixelRatio:1,addEventListener(){}},document:{querySelectorAll:()=>[]},fmt:v=>v==null?'Unavailable':String(v),eng:(v,u)=>v==null?'Unavailable':v+' '+u,pct:v=>v==null?'Unavailable':v+'%',job:'health-job',start:0,end:1,pause(){},plot:async()=>{},selectedFault:null,api:async url=>{requests.push(url);return {event:{id:5}}},focusFault:async event=>focused.push(event),fail:e=>{throw e}};
vm.createContext(context);vm.runInContext(fs.readFileSync('static/health.js','utf8'),context);
(async()=>{
 context.renderHealth(data);
 assert(el('#healthPhaseSummary').textContent.includes('drift slope'));
 assert(el('#healthZSummary').textContent.includes('Z relative to A'));
 assert(el('#healthDifferential').innerHTML.includes('A/-A'));
 assert(el('#healthSpeedSummary').textContent.includes('RPM'));
 assert(el('#healthAccelerationSummary').textContent.includes('RPM/s'));
 assert(el('#healthWidthSummary').textContent.includes('complete cycles'));
 assert(el('#healthNoise').innerHTML.includes('0.02 / 0.03 V'));
 assert.equal(el('#nextQuadratureFlag').disabled,false);assert.equal(el('#nextGlitchFlag').disabled,false);
 await el('#nextQuadratureFlag').onclick();await el('#nextGlitchFlag').onclick();
 assert(requests[0].includes('kind=ambiguous%20quadrature%20transition'));assert(requests[1].includes('kind=short%20raw%20pulse%20candidate'));assert.equal(focused.length,2);assert.equal(el('#chart').scrolled,true);assert(el('#healthGlitchStatus').textContent.includes('Showing'));
 await el('#healthPhaseChart').onclick({clientX:400});await new Promise(resolve=>setImmediate(resolve));assert(context.end-context.start<=.100001);
 context.summary.metrics=[{c:6,period:.1,base_period:.01}];await context.inspectHealthTime(.5);assert(Math.abs(context.end-context.start-.16)<1e-9);
 context.renderHealth({...data,quadrature:{available:true,simultaneous:0},noise:{A:{short_pulses:0}}});assert(el('#healthQuadratureStatus').textContent.includes('nothing to zoom'));assert(el('#healthGlitchStatus').textContent.includes('nothing to zoom'));
 context.renderHealth(null);assert.equal(el('#nextQuadratureFlag').disabled,true);assert.equal(el('#healthDifferential').innerHTML,'');
 console.log('Health UI checks passed: all panels, trend drawing, units, unavailable/reset states and filtered fault navigation.');
})().catch(e=>{console.error(e);process.exitCode=1});
