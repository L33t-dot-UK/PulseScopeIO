'use strict';
function healthTable(headers,rows){
    return '<div class="scroll"><table class="health-data"><thead><tr>'+headers.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}
function healthUnit(){return summary?.axis_unit==='s'?'s':summary?.axis_unit||'samples'}
function healthTime(v){return v==null?'Unavailable':eng(v,healthUnit())}
function healthSeries(canvasId,series,unit){
    const canvas=$('#'+canvasId),ctx=canvas.getContext('2d');
    if(!ctx)return;
    const width=Math.max(320,canvas.getBoundingClientRect().width||800),height=220,dpr=window.devicePixelRatio||1;
    canvas.width=width*dpr;canvas.height=height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.fillStyle='#171d20';ctx.fillRect(0,0,width,height);
    const usable=series.filter(s=>s?.points?.length);
    canvas.onclick=null;canvas.removeAttribute('data-interactive');
    ctx.font='11px monospace';ctx.fillStyle='#a9b4b8';
    if(!usable.length){ctx.fillText('No usable data for this check',18,35);return;}
    const points=usable.flatMap(s=>s.points),low=Math.min(...points.map(p=>p[2])),high=Math.max(...points.map(p=>p[3]));
    const padding=Math.max((high-low)*.12,Math.abs(high)*.01,unit==='°'||unit==='RPM/s'?.1:unit==='RPM'?1:1e-12),ymin=low-padding,ymax=high+padding;
    const xmin=summary.start,xmax=summary.end,px=t=>85+(t-xmin)/Math.max(xmax-xmin,1e-30)*(width-105),py=v=>185-(v-ymin)/(ymax-ymin)*165;
    for(let i=0;i<4;i++){
        const v=ymin+(ymax-ymin)*i/3,y=py(v);ctx.strokeStyle='#30393d';ctx.beginPath();ctx.moveTo(85,y);ctx.lineTo(width-20,y);ctx.stroke();
        ctx.fillStyle='#a9b4b8';ctx.fillText(unit==='s'?eng(v,'s'):Number(v.toPrecision(4)).toString()+' '+unit,4,y+4);
    }
    usable.forEach((s,index)=>{
        const color=s.color||['#4dd6e8','#ffb454'][index%2];ctx.strokeStyle=color;
        ctx.globalAlpha=.3;ctx.lineWidth=1;ctx.beginPath();s.points.forEach(p=>{ctx.moveTo(px(p[0]),py(p[2]));ctx.lineTo(px(p[0]),py(p[3]))});ctx.stroke();
        ctx.globalAlpha=1;ctx.lineWidth=1.6;ctx.beginPath();s.points.forEach((p,i)=>i?ctx.lineTo(px(p[0]),py(p[1])):ctx.moveTo(px(p[0]),py(p[1])));ctx.stroke();
        if(s.points.length===1){ctx.beginPath();ctx.arc(px(s.points[0][0]),py(s.points[0][1]),3,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();}
    });
    ctx.fillStyle='#a9b4b8';ctx.fillText(healthTime(xmin),85,210);ctx.fillText(healthTime(xmax),Math.max(100,width-120),210);
    canvas.setAttribute('data-interactive','true');
    canvas.title='Click a position on this trend to inspect the corresponding waveform.';
    canvas.onclick=event=>{
        const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left)*width/rect.width;
        if(x<85 || x>width-20)return;
        inspectHealthTime(xmin+(x-85)/(width-105)*(xmax-xmin));
    };
    canvas.setAttribute('aria-label',canvas.id.replace('health','').replace('Chart','')+' trend; '+usable.length+' series. Time runs from '+healthTime(xmin)+' to '+healthTime(xmax));
}
function renderHealthWidths(){
    const data=summary?.advanced_analysis?.widths||{},name=$('#healthWidthChannel').value,w=data[name];
    $('#healthWidthSummary').textContent=w?`${name} · ${fmt(w.high.count)} complete cycles · mean high ${healthTime(w.high.mean)}, mean low ${healthTime(w.low.mean)}`:'No complete channel cycles available.';
    healthSeries('healthWidthChart',w?[w.high,w.low]:[],healthUnit());
}
function drawHealth(){
    const d=summary?.advanced_analysis;
    if(!d)return;
    healthSeries('healthPhaseChart',[d.phase],'°');
    healthSeries('healthZChart',[d.z?.A,d.z?.B],'°');
    healthSeries('healthSpeedChart',[d.speed],'RPM');
    healthSeries('healthAccelerationChart',[d.speed?.acceleration],'RPM/s');
    renderHealthWidths();
}
function renderHealth(d){
    $('#healthScope').textContent=d?.scope||'Load a recording for whole-file signal diagnostics.';
    $('#nextQuadratureFlag').disabled=!d?.quadrature?.simultaneous;
    const glitches=Object.values(d?.noise||{}).reduce((sum,v)=>sum+v.short_pulses,0);
    $('#nextGlitchFlag').disabled=!glitches;
    $('#healthQuadratureStatus').textContent=!d?'Load a recording to check transitions.':d.quadrature?.simultaneous?'Select Zoom to move to a flagged transition on the waveform.':d.quadrature?.available?'No ambiguous transitions found; there is nothing to zoom to.':'A and B are required for this check.';
    $('#healthGlitchStatus').textContent=!d?'Load a recording to check short pulses.':glitches?'Select Zoom to move to a short pulse on the waveform.':'No short pulse candidates found; there is nothing to zoom to.';
    if(!d){
        for(const id of ['healthPhaseSummary','healthPhaseNote','healthZSummary','healthQuadrature','healthSpeedSummary','healthAccelerationSummary','healthSpeedNote','healthWidthSummary'])$('#'+id).textContent='';
        $('#healthDifferential').innerHTML='';$('#healthNoise').innerHTML='';$('#healthWidthChannel').innerHTML='';
        for(const id of ['healthPhaseChart','healthZChart','healthSpeedChart','healthAccelerationChart','healthWidthChart'])healthSeries(id,[], '');
        return;
    }
    const phase=d.phase;
    $('#healthPhaseSummary').textContent=phase.available?`${fmt(phase.count)} cycles · phase range ${fmt(phase.minimum)}° to ${fmt(phase.maximum)}° · first-to-last trend change ${fmt(phase.net_change)}° · drift slope ${fmt(phase.slope)}°/${healthUnit()}`:phase.note;
    $('#healthPhaseNote').textContent=phase.available?phase.note:'';
    const pairs=Object.entries(d.differential);
    $('#healthDifferential').innerHTML=pairs.length?healthTable(['Pair','Common-mode mean / range','Differential low / high','Level imbalance','Non-complementary samples','Opposite-edge skew mean / max |skew|','Unmatched edges'],pairs.map(([name,v])=>[name,`${fmt(v.common_mode_mean)} V / ${fmt(v.common_mode_minimum)}–${fmt(v.common_mode_maximum)} V`,`${fmt(v.differential_low)} / ${fmt(v.differential_high)} V`,pct(v.level_imbalance_percent),pct(v.noncomplementary_percent),`${healthTime(v.edge_skew.mean)} / ${healthTime(v.edge_skew.available?Math.max(Math.abs(v.edge_skew.minimum),Math.abs(v.edge_skew.maximum)):null)}`,fmt(v.unmatched_edges)])):'Both sides of a differential pair are required.';
    $('#healthDifferential').title=pairs[0]?.[1].note||'';
    $('#healthZSummary').textContent=Object.entries(d.z).map(([name,v])=>v.available?`Z relative to ${name}: ${fmt(v.count)} indexes · typical ${fmt(v.reference_degrees)}° · deviation range ${fmt(v.minimum)}° to ${fmt(v.maximum)}° · RMS deviation ${fmt(Math.sqrt(v.std*v.std+v.mean*v.mean))}°`:name+': '+v.note).join(' · ');
    const q=d.quadrature;
    $('#healthQuadrature').textContent=q.available?`${fmt(q.valid_transitions)} valid sampled transitions · ${fmt(q.simultaneous)} ambiguous two-bit transitions · ${fmt(q.direction_changes)} changes in observed transition direction`:q.note;
    $('#healthQuadrature').classList.toggle('health-warning',q.simultaneous>0);
    const speed=d.speed,acc=speed.acceleration;
    $('#healthSpeedSummary').textContent=speed.available?`${fmt(speed.count)} revolutions · mean ${fmt(speed.mean)} RPM · range ${fmt(speed.minimum)}–${fmt(speed.maximum)} RPM · peak-to-peak variation ${pct(speed.variation_percent)}`:speed.note;
    $('#healthAccelerationSummary').textContent=acc?.available?`Revolution-average acceleration: ${fmt(acc.minimum)} to ${fmt(acc.maximum)} RPM/s`:'';
    $('#healthSpeedNote').textContent=speed.available?speed.note:'';
    const selected=$('#healthWidthChannel').value;
    $('#healthWidthChannel').innerHTML=Object.keys(d.widths).map(name=>'<option value="'+name+'">'+name+'</option>').join('');
    if(d.widths[selected])$('#healthWidthChannel').value=selected;
    $('#healthNoise').innerHTML=healthTable(['Channel','Low / high plateau RMS variation','Raw / analysed transitions','Extra raw crossings','Short pulse candidates','Low / high width limit'],Object.entries(d.noise).map(([name,v])=>[name,`${fmt(v.low_rms)} / ${fmt(v.high_rms)} V`,fmt(v.raw_transitions)+' / '+fmt(v.analysed_transitions),fmt(v.extra_raw_crossings),fmt(v.short_pulses),`${healthTime(v.low_width_limit)} / ${healthTime(v.high_width_limit)}`]));
    drawHealth();
}
function revealHealthWaveform(){
    $('#chart').scrollIntoView({behavior:'smooth',block:'center'});
}
async function inspectHealthTime(time){
    if(!summary)return;
    const metric=summary.metrics?.find(m=>[6,7].includes(m.c)&&m.period>0);
    const period=metric?.base_period ?? metric?.period;
    const span=period ? Math.min(16*period,summary.end-summary.start) : Math.min(end-start,(summary.end-summary.start)/10);
    start=Math.max(summary.start,Math.min(time-span/2,summary.end-span));
    end=Math.min(summary.end,start+span);
    pause();selectedFault=null;
    try{await plot();revealHealthWaveform()}catch(e){fail(e)}
}
async function focusHealthFlag(kind){
    if(!summary)return;
    const button=kind==='ambiguous quadrature transition'?$('#nextQuadratureFlag'):$('#nextGlitchFlag');
    const feedback=kind==='ambiguous quadrature transition'?$('#healthQuadratureStatus'):$('#healthGlitchStatus');
    if(button.disabled)return;
    button.disabled=true;
    feedback.textContent='Locating the next event...';
    try{
        const after=selectedFault?.kind===kind?`&after_time=${selectedFault.time}&after_id=${selectedFault.id}`:'';
        const d=await api('/api/next-fault?id='+job+'&kind='+encodeURIComponent(kind)+after);
        if(!d.event){feedback.textContent='No matching events found in this recording.';return;}
        pause();
        await focusFault(d.event);
        feedback.textContent=`Showing ${d.event.channels?'A/B':d.event.channel} · ${kind} at ${healthTime(d.event.time)}${d.event.width!=null?' · width '+healthTime(d.event.width):''}. The waveform is highlighted above.`;
        revealHealthWaveform();
    }catch(e){feedback.textContent=e.message;fail(e)}
    finally{button.disabled=false;}
}
$('#nextQuadratureFlag').onclick=()=>focusHealthFlag('ambiguous quadrature transition');
$('#nextGlitchFlag').onclick=()=>focusHealthFlag('short raw pulse candidate');
$('#healthWidthChannel').onchange=renderHealthWidths;
document.querySelectorAll('.health-check').forEach(el=>el.addEventListener('toggle',drawHealth));
window.addEventListener('resize',drawHealth);

if(summary)renderHealth(summary.advanced_analysis);
