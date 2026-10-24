'use strict';
const $ = s => document.querySelector(s),
    names = ['A', '−A', 'B', '−B', 'Z', '−Z'],
    colors = ['#4dd6e8', '#ffb454', '#50d697', '#b394ff', '#ff6178', '#eee16b'];
let selectedFault = null;
let binaryLoading = false;
let csvChannelLoading = false;
let measured = null,
    preTrimSpan = null,
    editTimer, pendingAnalysis = false,
    queuedOptions = null;
let replayLoops = 0;
let token = '',
    job = null,
    summary = null,
    view = null,
    start = 0,
    end = 1,
    timer = null,
    playing = false,
    playTime = 0,
    request = 0;
let loadingTask = null;
let playGeneration = 0;
let navigatorData = null;
let mergeSubmissionPending = false, alignmentChoiceResolve = null;
let cacheInfo = null, cacheRevision = null, cacheRequest = 0, cacheRefreshPending = false, deletingCache = false;

function cacheSizeLabel(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return 'Unavailable';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let value = bytes, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return (unit ? value.toFixed(value < 10 ? 2 : 1) : String(Math.round(value))) + ' ' + units[unit];
}

function syncCacheControls() {
    const reason = deletingCache || cacheInfo?.clearing ? 'Cache deletion is in progress.' :
        pendingAnalysis || loadingTask ? 'Wait for analysis or export preparation to finish.' :
        !cacheInfo ? 'Cache size is not available yet.' :
        cacheInfo.busy ? 'Wait for active analysis, export or recording requests to finish.' : '';
    $('#deleteCache').disabled = !!reason;
    $('#deleteCache').title = reason || 'Delete cached analyses and generated recordings; close loaded recordings.';
}

function resetAfterCacheClear(message) {
    pause();
    finishAlignmentChoice(null);
    clearTimeout(editTimer); clearTimeout(rpmEditTimer);
    request++; cacheRequest++;
    job = summary = view = measured = navigatorData = selectedFault = preTrimSpan = null;
    $('#csvColumnStatus').hidden=true;
    $('#bearingTestStatus').hidden=true;
    renderPhaseAnalysis(null);
    if(typeof renderHealth==='function')renderHealth(null);
    if(typeof renderCsvColumns==='function')renderCsvColumns(null);
    pendingAnalysis = false; queuedOptions = null; replayLoops = 0;
    start = playTime = 0; end = 1;
    hideLoading();
    for (const id of ['exportCsv', 'exportPdf', 'zoomErrors', 'trimBeforeFirstZ']) $('#' + id).disabled = true;
    document.querySelectorAll('.radar-buttons button').forEach(b => b.disabled = true);
    for (const id of ['metrics', 'checks', 'legend', 'channelPlots', 'jitterPlots', 'radarPulseDigits']) $('#' + id).innerHTML = '';
    $('#table tbody').innerHTML = '<tr><td colspan="11">Load a recording to calculate measurements.</td></tr>';
    document.querySelectorAll('canvas').forEach(c => c.getContext('2d')?.clearRect(0, 0, c.width, c.height));
    $('#progress').value = 0;
    $('#status').textContent = message;
    status(message);
    try { window.sessionStorage?.setItem('encoder-cache-notice', message); } catch (_) {}
    syncCacheControls();
    window.location.reload();
}

async function refreshCache(repeat = false) {
    if (cacheRefreshPending) {
        if (repeat) setTimeout(() => refreshCache(true), 10000);
        return;
    }
    cacheRefreshPending = true;
    const seq = ++cacheRequest;
    try {
        const d = await api('/api/cache');
        if (seq !== cacheRequest) return;
        if (!Number.isFinite(d.bytes) || typeof d.revision !== 'string') throw Error('Cache status unavailable');
        const changed = cacheRevision !== null && d.revision !== cacheRevision;
        cacheInfo = d;
        cacheRevision = d.revision;
        $('#cacheSize').textContent = 'Cache: ' + cacheSizeLabel(d.bytes);
        $('#cacheSize').title = `${d.files.toLocaleString()} cached files. Size refreshes every 10 seconds.`;
        syncCacheControls();
        if (changed && !deletingCache) resetAfterCacheClear('Cache cleared in another tab. Reload a recording to continue.');
    } catch (_) {
        if (seq === cacheRequest) {
            cacheInfo = null;
            $('#cacheSize').textContent = 'Cache: unavailable';
            syncCacheControls();
        }
    } finally {
        cacheRefreshPending = false;
        if (repeat) setTimeout(() => refreshCache(true), 10000);
    }
}

$('#deleteCache').onclick = async () => {
    if (deletingCache || pendingAnalysis || loadingTask || !cacheInfo || cacheInfo.busy || cacheInfo.clearing) return;
    pause(); deletingCache = true; syncCacheControls();
    showLoading('cache', 'Deleting cache', 'Closing cached recordings and removing cached files...');
    try {
        const d = await api('/api/clear-cache', {});
        cacheInfo = d; cacheRevision = d.revision;
        $('#cacheSize').textContent = 'Cache: ' + cacheSizeLabel(d.bytes);
        const message = d.complete ? 'Cache deleted. Reload a recording to continue.' :
            `Cache partly deleted; ${cacheSizeLabel(d.bytes)} remains. Close other programs using cached files and retry.`;
        resetAfterCacheClear(message);
    } catch (e) {
        hideLoading('cache'); status(e.message);
        await refreshCache();
    } finally {
        deletingCache = false; syncCacheControls();
    }
};


function showLoading(kind, title, message) {
    loadingTask = kind;
    syncCacheControls();
    $('#loadingTitle').textContent = title;
    $('#loadingMessage').textContent = message;
    $('#loadingCancel').hidden = kind !== 'analysis';
    $('#loadingCancel').disabled = true;
    $('#loadingOverlay').setAttribute('aria-hidden', 'false');
    updateLoading(kind, null, message);
    if (!$('#loadingOverlay').open) $('#loadingOverlay').showModal();
}

function updateLoading(kind, progress, message) {
    if (loadingTask !== kind) return;
    $('#loadingMessage').textContent = message;
    const known = progress != null && Number.isFinite(progress),
        value = known ? Math.max(0, Math.min(100, progress * 100)) : 0;
    $('#loadingRing').classList.toggle('indeterminate', !known);
    $('#loadingPercent').textContent = known ? Math.round(value) + '%' : '...';
    $('#loadingArc').style.strokeDashoffset = String(201.062 * (1 - value / 100));
    if (known) $('#loadingProgress').setAttribute('aria-valuenow', String(Math.round(value)));
    else $('#loadingProgress').removeAttribute('aria-valuenow');
    if (kind === 'analysis') $('#loadingCancel').disabled = !pendingAnalysis;
}

function hideLoading(kind) {
    if (kind && loadingTask !== kind) return;
    loadingTask = null;
    syncCacheControls();
    $('#loadingOverlay').close();
    $('#loadingOverlay').setAttribute('aria-hidden', 'true');
}
$('#loadingOverlay').addEventListener('cancel', e => e.preventDefault());
$('#loadingCancel').onclick = () => {
    if (loadingTask === 'analysis' && pendingAnalysis) {
        $('#loadingCancel').disabled = true;
        $('#loadingMessage').textContent = 'Cancelling analysis...';
        $('#cancelJob').click()
    }
};
async function api(url, body) {
    const r = await fetch(url, body === undefined ? {} : {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Local-Token': token
        },
        body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) throw Error(d.error || r.statusText);
    return d
}

function status(s) {
    $('#jobStatus').textContent = s
}

function fail(e) {
    if (csvChannelLoading) {$("#csvChannelError").textContent = e.message; $("#csvChannelError").hidden = false; csvChannelLoading = false;}
    if (binaryLoading) {$("#binError").textContent = e.message; $("#binError").hidden = false; binaryLoading = false;}
    hideLoading();
    pendingAnalysis = false;
    queuedOptions = null;
    if (summary) job = summary.id;
    syncCacheControls();
    refreshCache();
    status(e.message);
    $('#status').textContent = e.message
}

function syncRawPlotMode() {
    const smoothing=$('#smoothEdges').checked;
    if(smoothing)$('#rawPlot').checked=false;
    $('#rawPlot').disabled=smoothing;
}
function options() {
    return {
        auto_mode: $('#autoMode').value,
        phase_tolerance_percent: +$('#phaseTolerance').value,
        delimiter: $('#delimiter').value,
        timecol: $('#timecol').value,
        low: +$('#lowThreshold').value,
        high: +$('#highThreshold').value,
        window: +$('#smoothWindow').value,
        smooth_edges: $('#smoothEdges').checked,
        reference: $('#expectedPprEnabled').checked,
        ppr: $('#expectedPpr').value,
        rpm: $('#rpm').value,
        slew_mode: $('#slewMode').value,
        receiver_mode: $('#receiverMode').value,
        smoothing_mode: $('#smoothingMode').value,
        edge_mode: $('#edgeMode').value,
        alignment_mode: summary?.options?.alignment_mode === 'refine' ? 'refine' : 'check'
    }
}
async function open(path, auto = false) {
    pause();
    showLoading("analysis", "Loading recording", "Opening and analysing the complete file...");
    try {
        let d = await api('/api/open', {
            path,
            options: {
                ...options(),
                auto
            }
        });
        job = d.id;
        pendingAnalysis = true;
        poll()
    } catch (e) {
        fail(e)
    }
}
async function poll() {
    try {
        let d = await api('/api/status?id=' + job);
        $('#progress').value = d.progress;
        updateLoading('analysis', d.progress, d.stage);
        status(d.stage + ' · ' + (100 * d.progress).toFixed(0) + '%');
        if (d.error) throw Error(d.error);
        if (!d.ready) {
            setTimeout(poll, 400);
            return
        }
        binaryLoading = false;
        csvChannelLoading = false;
        summary = d;
        measured = null;
        preTrimSpan = null;
        pendingAnalysis = false;
        syncCacheControls();
        refreshCache();
        selectedFault = null;
        replayLoops = 0;
        if (queuedOptions) {
            const next = queuedOptions;
            queuedOptions = null;
            await reanalyse(false, next);
            return;
        }
        start = d.start;
        end = d.end;
        playTime = start;
        $('#smoothWindow').value = d.window || d.options.window || 1;
        $('#smoothWindowValue').textContent = d.window;
        $('#smoothWindowNote').textContent = `Effective ${d.window}-sample average${d.window<d.requested_window?' (capped to preserve pulse spacing)':''}`;
        renderSummary();
        updateLoading('analysis', 1, 'Preparing waveforms and analysis results...');
        await plot();
        hideLoading('analysis');
    } catch (e) {
        fail(e)
    }
}

function fmt(n) {
    return n == null ? 'Unavailable' : Number(n).toLocaleString(undefined, {
        maximumSignificantDigits: 6
    })
}

function renderPulseCounts(s, page = s.pulse_counts) {
    const box = $('#missedPulses');
    box.className = 'missed-box ' + (s.error_count ? 'bad' : 'good');
    const header = `${page.missing_total.toLocaleString()} missing pulses | ${s.error_count} pulse-count discrepancies | ${page.revolutions} complete revolutions`;
    const reference = `Reference ${fmt(s.diagnostics.reference_ppr)} (${s.options.reference ? 'entered' : 'most common observed count'}). Counts cover complete Z-to-Z revolutions; partial revolutions are excluded. A and B are counted separately; inverted channels are not counted twice.`;
    box.innerHTML = '<b>' + header + '</b><p>' + reference + '</p>' +
        (page.rows.length ? '<ul>' + page.rows.map(r => `<li>Revolution ${r.revolution}: <b>${r.total} missing pulses</b> (${Object.entries(r.missing).map(([channel, count]) => channel + ': ' + count).join(', ')})</li>`).join('') + '</ul>' : '<p>No complete revolutions available.</p>') +
        (page.revolutions > 200 ? `<p>Revolutions ${page.offset + 1}-${page.offset + page.rows.length} of ${page.revolutions}</p><button id="pulseCountsPrevious" ${page.offset === 0 ? 'disabled' : ''}>Previous revolutions</button> <button id="pulseCountsNext" ${page.next_offset == null ? 'disabled' : ''}>Next revolutions</button>` : '');
    async function showPage(offset) {
        try {
            const data = await api('/api/pulse-counts?id=' + s.id + '&offset=' + offset);
            if (summary?.id === s.id) renderPulseCounts(s, data);
        } catch (e) { fail(e) }
    }
    if (page.revolutions > 200) {
        $('#pulseCountsPrevious').onclick = () => showPage(Math.max(0, page.offset - 200));
        $('#pulseCountsNext').onclick = () => showPage(page.next_offset);
    }
}

function renderPhaseAnalysis(p) {
    const details=$('#phaseAnalysisDetails');
    $('#nextPhaseOutlier').disabled=!p?.outliers || $('#hidePhaseFaults').checked;
    if(p?.tolerance_percent!=null){$('#phaseTolerance').value=p.tolerance_percent;$('#phaseToleranceValue').textContent=p.tolerance_percent+'%';}
    $('#phaseOutlierDetails').textContent='';
    details.style.color=p?.outliers?'#ff9aab':'';
    details.title=p?.note || '';
    $('#phaseAnalysis').classList.toggle('bad',!!p?.outliers);
    $('#phaseAnalysis').classList.toggle('good',!!p?.available && !p.outliers);
    details.textContent=p?.available ? `Typical A/B phase: ${fmt(p.typical_degrees)}° · ${p.outliers.toLocaleString()} unusual cycles / ${p.cycles.toLocaleString()} checked · Largest deviation: ${fmt(p.max_deviation_degrees)}° · ${p.tolerance_percent ?? 10}% phase threshold: ${fmt(p.tolerance_degrees)}° · ${p.excluded_cycles.toLocaleString()} ambiguous cycles excluded. Tolerance also allows for three samples of timing resolution.` : (p?.note || 'Load A and B to compare phase across individual cycles.');
}
$('#nextPhaseOutlier').onclick=async()=>{
    if(!summary?.phase_analysis?.outliers || $('#hidePhaseFaults').checked)return;
    try {
        const q=selectedFault?.kind==='A/B phase outlier'?`&after_time=${selectedFault.time}&after_id=${selectedFault.id}`:'';
        const d=await api('/api/next-fault?id='+job+'&kind='+encodeURIComponent('A/B phase outlier')+q);
        await focusFault(d.event);
    }catch(e){fail(e)}
};

function renderMergeStatus(m) {
    const alignment = m?.alignment_check;
    const refinement = m?.refinement || m?.phase_refinement;
    const refined = refinement?.applied === true;
    const phaseDetails = alignment?.time_scale != null ? `Reference time scale ${fmt(alignment.time_scale)} (${alignment.time_scale_unit}); sample scale ${fmt(alignment.sample_scale)}; merged phase ${fmt(alignment.merged_phase_degrees)} deg, reference phase ${fmt(alignment.reference_phase_degrees)} deg; difference ${fmt(alignment.phase_difference_degrees)} deg (tolerance ${fmt(alignment.tolerance_degrees)} deg). ` : '';
    $('#alignmentStatus').textContent = alignment ? `${refined ? 'Reference-fit result' : 'A/B alignment check'} (${alignment.source}): ${alignment.status.toUpperCase()}. ${phaseDetails}${alignment.reason || ''} ${refined ? 'B timing was adjusted using this reference. Agreement is a constructed fit, not independent verification. ' : ''}${alignment.note || ''}` : '';
    $('#alignmentStatus').style.color = refined ? '#ffd38b' : alignment?.status === 'mismatch' ? '#ff8291' : alignment?.status === 'match' ? '#71e3ab' : '';
    const timing = refined ? `B TIMING REFINED from ${refinement.source || alignment?.source || 'the third capture'}${refinement.target_phase_degrees != null ? ' to reference phase ' + fmt(refinement.target_phase_degrees) + ' deg' : ''}. Waveforms, measurements and downloaded CSV contain the adjusted B timing; source files are kept.` : m?.resampled_b ? 'B timing synchronized between corresponding Z pulses on the A time grid; the output retains the verified Z span.' : 'Direct sample alignment; B samples preserved.';
    $('#mergeStatus').textContent = m ? `Merged ${m.sources.join(' + ')} | ${m.retained_samples.toLocaleString()} samples | initial B offset ${m.offset_samples} samples | ${m.matched_z} matched Z pulses | source Z drift ${m.max_z_mismatch_samples} samples before correction. ${timing} Z/-Z from capture A. ${m.warning || ''} CSV download includes all six channels.` : 'Synchronizes corresponding complete Z pulses. Z/-Z comes from capture A. CSV download contains all six merged channels.';
}

function renderSummary() {
    syncRawPlotMode();
    let s = summary;
    renderMergeStatus(s.merge_info);
    const bearing=s.options.bearing_test;
    $('#bearingTestStatus').hidden=!bearing;
    $('#bearingTestStatus').textContent=bearing?`Generated bearing-error test · ${fmt(bearing.error_degrees)}° lag · ${bearing.missing_cycles} complete A/B cycles omitted · ${bearing.ppr} cycles/revolution · next Z resets at ${bearing.reset_time} s · seed ${bearing.seed}. ${bearing.note}`:'';
    $('#csvColumnStatus').hidden=!s.csv_columns?.assumed;
    $('#csvColumnStatus').textContent=s.csv_columns?.assumed ? s.csv_columns.note + (s.csv_columns.assignments ? ' Assignments: '+Object.entries(s.csv_columns.assignments).map(([name,col])=>name+' = column '+col).join(', '):'') : '';
    $("#binAlignmentStatus").textContent = s.bin_alignment ? `Assumed ${s.bin_alignment.target_degrees}° alignment · measured ${fmt(s.bin_alignment.phase_degrees)}° · B shift ${s.bin_alignment.shift_samples} samples. ${s.bin_alignment.note}` : "";
    $("#binAlignmentStatus").style.color = "#ffd38b";
    const timebase=s.bin_timebase, warning=$('#binTimebaseStatus');
    warning.hidden=!timebase?.applied;
    warning.textContent=timebase?.applied ? `Time-base adjustment applied · reference ${timebase.reference_channel}. ${timebase.scaled_channels.map(c=>c.channel+': '+fmt(c.source_rate)+' → '+fmt(c.target_rate)+' samples/s').join('; ')}${timebase.rebased_channels.length ? ' · Capture start rebased: '+timebase.rebased_channels.join(', ') : ''}${timebase.trimmed ? ' · Trimmed to common duration.' : ''} ${timebase.note}` : '';


    $('#status').textContent = s.filename + ' · ' + s.count.toLocaleString() + ' samples · whole file analysed';
    $('#expectedPprEnabled').checked = !!s.options.reference;
    $('#expectedPpr').disabled = !s.options.reference;
    $('#expectedPprStatus').textContent = s.options.reference ? 'Entered reference enabled' : 'Automatic modal reference';
    $('#lowThreshold').value = s.low;
    $('#highThreshold').value = s.high;
    renderMeasurements(s);
    renderPulseCounts(s);
    renderPhaseAnalysis(s.phase_analysis);
    if(typeof renderHealth==='function')renderHealth(s.advanced_analysis);
    if(typeof renderCsvColumns==='function')renderCsvColumns(s);
    $('#timecol').innerHTML = '<option value="auto">Auto detect</option><option value="index">Sample index</option>' + s.headers.map((h, i) => `<option value="${i}"></option>`).join('');
    [...$('#timecol').options].slice(2).forEach((o, i) => o.textContent = s.headers[i]);
    $('#timecol').value = s.options.timecol || 'auto';
    for (const id of ['exportCsv', 'exportPdf', 'zoomErrors', 'trimBeforeFirstZ']) $('#' + id).disabled = false;
    let suitable = s.replay_available;
    $('#replayPpr').value = s.replay_ppr;
    document.querySelectorAll('.radar-buttons button').forEach(b => b.disabled = !suitable);
    $('#errorNavStatus').textContent = `${s.error_count} discrepancies · first ${s.errors.length} available as flags`;
    $('#checks').innerHTML = s.present.map(c => `<label><input type="checkbox" data-channel="${c}" checked>${names[c]}</label>`).join('');
    $('#checks').onchange = draw;
    $('#legend').innerHTML = s.present.map(c => `<span style="color:${colors[c]}">${names[c]}</span>`).join('');
    renderMinis();
    position();
}

function eng(value, unit = 's') {
    if (value == null) return 'Unavailable';
    if (unit === 's' || unit === 'Hz') {
        if (value === 0) return '0 ' + unit;
        const units = unit === 's' ? ['fs', 'ps', 'ns', '\u00b5s', 'ms', 's', 'ks', 'Ms', 'Gs', 'Ts'] : ['pHz', 'nHz', '\u00b5Hz', 'mHz', 'Hz', 'kHz', 'MHz', 'GHz', 'THz'];
        const base = unit === 's' ? 5 : 4,
            index = Math.max(0, Math.min(units.length - 1, Math.floor(Math.log10(Math.abs(value)) / 3) + base));
        return Number((value / 1000 ** (index - base)).toPrecision(4)) + ' ' + units[index];
    }
    return Number(value.toPrecision(4)) + ' ' + unit;
}

function pct(v) {
    return v == null ? 'Unavailable' : v.toFixed(1) + '%';
}

function renderMeasurements(m) {
    measured = m;
    const d = m.diagnostics,
        unit = d.display_unit;
    const duration = (m.end - m.start) * (d.time_scale ?? 1);
    const phaseQuality = d.phase_coherence != null && (d.phase_coherence < 0.9 || d.phase_coverage < 0.8) ? 'Varies / limited paired cycles · mean ' : '';
    $('#metrics').innerHTML = [
        ['Samples in span', fmt(m.count)],
        ['Capture sample rate', d.sample_rate == null ? 'Unavailable' : fmt(d.sample_rate) + ' samples/s'],
        ['RPM · ' + m.rpm_source, fmt(m.rpm)],
        ['Position PPR', m.ppr ? fmt(m.ppr) : 'Unavailable'],
        ['Channel OOS cycles (45–55%)', fmt(d.oos_cycles)],
        ['Selected span at speed', eng(duration, unit)],
        ['Mean duty cycle', pct(d.mean_duty)],
        ['Mean period', eng(d.mean_period, unit)],
        ['Angular period', d.angular_period == null ? 'Unavailable' : fmt(d.angular_period) + '°'],
        ['Pulses for 1° rotation', fmt(d.pulses_per_degree)],
        ['A/B phase (A leads positive)', d.phase_degrees == null ? 'Unavailable' : phaseQuality + fmt(d.phase_degrees) + '° · ' + eng(d.phase_dt, unit) + ' · ' + fmt(d.phase_pairs) + ' cycles']
    ].concat(Object.entries(d.differential_vpp || {}).map(([pair, value]) => [pair + ' mean peak-to-peak', value.volts == null ? 'Unavailable' : fmt(value.volts) + ' V'])).map(([k, v]) => `<div class="metric" ${k.startsWith('RPM')?'id="rpmMetric"':''}><small ${k.includes('peak-to-peak') ? 'title="Mean of the raw differential maximum minus minimum in each complete rising-edge cycle; volts."' : ''}>${k}</small><strong>${v}</strong></div>`).join('');
    $('#measurementScopeStatus').textContent = $('#measurementScope').value === 'visible' ? ' Complete cycles in the visible span' : ' Full recording analysed';
    $('#table tbody').innerHTML = m.metrics.filter(raw => raw.c < 6).map(raw => '<tr>' + [raw.channel, ...['rise', 'fall', 'high', 'low', 'period'].map(k => eng(raw[k], raw.time_unit)), fmt(raw.ppr), eng(raw.frequency, raw.frequency_unit), (raw.c >= 4 ? 'Not applicable' : pct(raw.duty)), raw.c >= 4 ? 'Excluded' : raw.oos, raw.edges].map(x => '<td>' + x + '</td>').join('') + '</tr>').join('');
}
async function updateMeasurements(seq) {
    if (!summary) return;
    if ($('#measurementScope').value === 'whole') {
        renderMeasurements(summary);
        return;
    }
    const d = await api(`/api/measurements?id=${job}&start=${start}&end=${end}`);
    if (seq === request) renderMeasurements(d);
}
async function plot() {
    if (!summary) return;
    if (end <= start) {
        status('End must be greater than start');
        return
    }
    const seq = ++request;
    start = Math.max(summary.start, Math.min(start, summary.end));
    end = Math.min(summary.end, Math.max(end, start + Number.EPSILON));
    $('#tstart').value = start;
    $('#tend').value = end;
    try {
        let d = await api(`/api/plot?id=${job}&start=${start}&end=${end}`);
        if (seq !== request) return;
        view = d;
        if (start === summary.start && end === summary.end) navigatorData = {id: job, points: d.points};
        await updateMeasurements(seq);
        if (seq !== request) return;
        const factor = (summary.end - summary.start) / (end - start);
        $('#zoomreadout').textContent = factor.toFixed(1) + '×';
        $('#xzoom').value = Math.min(100, 25 * Math.log10(factor));
        showFaultDetails();
        draw();
        renderMinis()
    } catch (e) {
        fail(e)
    }
}

function canvas(id) {
    let c = $('#' + id),
        r = c.getBoundingClientRect();
    c.width = Math.max(100, r.width) * devicePixelRatio;
    c.height = Math.max(100, r.height) * devicePixelRatio;
    let x = c.getContext('2d');
    x.scale(devicePixelRatio, devicePixelRatio);
    x.clearRect(0, 0, r.width, r.height);
    return [x, r.width, r.height]
}

function isReceiverFault(event){
    if(event.signal_domain!=null)return event.signal_domain==='receiver';
    // Legacy cached events: only known checks measured on Rx logic are eligible.
    return ['missing pulses','extra pulses','A/B phase outlier','ambiguous quadrature transition'].includes(event.kind)
        || (event.kind==='duty outside 45–55%' && event.channel.startsWith('Rx '));
}
function faultsForDiagram(recovered){
    const flags=[...((recovered?view.receiver_faults:view.faults)?.events || (recovered?view.faults?.events:[]) || [])];
    if(selectedFault && !flags.some(e=>e.id===selectedFault.id))flags.push(selectedFault);
    return flags.filter(e=>(!recovered || isReceiverFault(e)) && (!$('#hidePhaseFaults').checked || e.kind!=='A/B phase outlier'));
}
function waveformVoltageScale(points,channel){
    let minimum=Infinity,maximum=-Infinity;
    for(const point of points){
        minimum=Math.min(minimum,point[2][channel]);
        maximum=Math.max(maximum,point[3][channel]);
    }
    if(!Number.isFinite(minimum)||!Number.isFinite(maximum))return {min:0,range:1};
    // Scale to every raw envelope extreme, with space beyond both rails so
    // positive overshoot and negative noise are not pressed against lane edges.
    const padding=Math.max((maximum-minimum)*.12,Math.max(Math.abs(minimum),Math.abs(maximum))*.01,1e-6);
    return {min:minimum-padding,range:maximum-minimum+2*padding};
}
function draw() {
    if (!view) return;
    for (let recovered of [false, true]) {
        let [x, w, h] = canvas(recovered ? 'rxChart' : 'chart');
        x.fillStyle = '#0b1020';
        x.fillRect(0, 0, w, h);
        let channels = recovered ? [6, 7, 8] : summary.present.filter(c => $('#checks input[data-channel="' + c + '"]').checked),
            lane = (h - 35) / Math.max(1, channels.length);
        channels.forEach((c, j) => {
            let y = 15 + j * lane;
            x.strokeStyle = '#28344b';
            x.beginPath();
            x.moveTo(45, y + lane);
            x.lineTo(w, y + lane);
            x.stroke();
            x.fillStyle = recovered ? colors[(c - 6) * 2] : colors[c];
            x.fillText(recovered ? ['Rx A', 'Rx B', 'Rx Z'][c - 6] : names[c], 2, y + 15);
            x.strokeStyle = x.fillStyle;
            x.beginPath();
            let range = 1,
                min = 0;
            if (!recovered && $('#rawPlot').checked) {
                const scale=waveformVoltageScale(view.points,c);
                min=scale.min;range=scale.range;
            }
            let previousY = null;
            view.points.forEach((p, k) => {
                let px = 45 + (p[0] - start) / (end - start) * (w - 55),
                    a = recovered ? p[4][c] : ($('#rawPlot').checked ? p[2][c] : p[4][c]),
                    b = recovered ? p[5][c] : ($('#rawPlot').checked ? p[3][c] : p[5][c]);
                let ya = y + lane - 8 - (a - min) / range * (lane - 20),
                    yb = y + lane - 8 - (b - min) / range * (lane - 20);
                if (!k) x.moveTo(px, ya);
                else {
                    if (recovered || !$('#rawPlot').checked) x.lineTo(px, previousY);
                    x.lineTo(px, ya);
                }
                x.lineTo(px, yb)
                previousY = yb;
            });
            x.stroke();
            if (!recovered && $('#rawPlot').checked && view.points.length < 450 && view.points.every(p => p[0] === p[1])) {
                x.fillStyle = colors[c];
                view.points.forEach(p => {
                    let px = 45 + (p[0] - start) / (end - start) * (w - 55),
                        py = y + lane - 8 - (p[2][c] - min) / range * (lane - 20);
                    x.beginPath();
                    x.arc(px, py, 2, 0, Math.PI * 2);
                    x.fill()
                });
            }
        });
        x.fillStyle = '#9eacc5';
        x.fillText(fmt(start), 45, h - 4);
        x.fillText(fmt(end) + (' ' + summary.axis_unit), Math.max(80, w - 150), h - 4);
        if (!$('#hideErrorMarkers').checked) {
            faultsForDiagram(recovered).flatMap(e => e.channels ? e.channels.map(channel=>({...e,channel})) : [e]).forEach(e => {
                if ((e.end_time ?? e.time) < start || e.time > end) return;
                const channel = e.channel.replace('Rx ', '').replace('−', '-');
                const rawIndex = ['A', '-A', 'B', '-B', 'Z', '-Z'].indexOf(channel);
                const c = recovered ? (rawIndex < 0 ? -1 : 6 + Math.floor(rawIndex / 2)) : rawIndex;
                const row = channels.indexOf(c);
                if (row < 0) return;
                const px = t => 45 + (t - start) / (end - start) * (w - 55);
                const left = Math.max(45, px(e.time)),
                    right = Math.min(w - 10, px(e.end_time ?? e.time));
                const active = selectedFault?.id === e.id;
                x.fillStyle = active ? 'rgba(255,80,110,.32)' : 'rgba(255,80,110,.14)';
                x.fillRect(left, 15 + row * lane, Math.max(3, right - left), lane - 3);
                x.strokeStyle = active ? '#fff0a6' : '#ff6178';
                x.lineWidth = active ? 2 : 1;
                x.strokeRect(left, 15 + row * lane, Math.max(3, right - left), lane - 3);
                x.lineWidth = 1;
                x.fillStyle = '#ff9aab';
                x.fillText(String(e.id), left, 12);
            });
        }
    }
    drawReplayCursor();
}

function drawReplayCursor() {
    for (const id of ['playbackCursor', 'rxPlaybackCursor']) {
        const [ctx, width, height] = canvas(id);
        const overlay = $('#' + id);
        overlay.dataset.replayTime = String(playTime);
        if (!summary || !view || playTime < view.start || playTime > view.end) continue;
        const x = 45 + (playTime - view.start) / (view.end - view.start) * (width - 55);
        ctx.strokeStyle = '#50ff80';
        ctx.lineWidth = 2;
        ctx.lineCap = 'round';
        ctx.setLineDash([1, 5]);
        ctx.beginPath();
        ctx.moveTo(x, 15);
        ctx.lineTo(x, height - 20);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#50ff80';
        ctx.font = '11px system-ui';
        ctx.fillText('Replay', Math.min(x + 6, width - 44), 27);
    }
    drawNavigator();
}

function navigatorWindow(left, span, minimum, maximum) {
    const width = Math.min(span, maximum - minimum);
    if (!(width > 0) || !Number.isFinite(left + width + minimum + maximum)) return null;
    const first = Math.max(minimum, Math.min(left, maximum - width));
    return [first, first + width];
}

function drawNavigator() {
    if (!summary || !navigatorData || navigatorData.id !== job) return;
    const [ctx, w, h] = canvas('waveformNavigator'), total = summary.end - summary.start;
    if (!(total > 0)) return;
    const left = 45, right = w - 14, width = right - left;
    const px = t => left + (t - summary.start) / total * width;
    ctx.fillStyle = '#0b1020'; ctx.fillRect(0, 0, w, h);
    const channels = summary.present, lane = (h - 26) / Math.max(1, channels.length);
    for (const [i, c] of channels.entries()) {
        const top = 4 + i * lane;
        const low = Math.min(...navigatorData.points.map(p => p[2][c]));
        const range = Math.max(...navigatorData.points.map(p => p[3][c])) - low || 1;
        ctx.fillStyle = colors[c]; ctx.font = '10px system-ui'; ctx.fillText(names[c], 5, top + lane * .7);
        ctx.strokeStyle = colors[c]; ctx.globalAlpha = .65; ctx.beginPath();
        let previous = null;
        for (const p of navigatorData.points) {
            const x = px((p[0] + p[1]) / 2);
            const a = top + lane - 3 - (p[2][c] - low) / range * (lane - 5);
            const b = top + lane - 3 - (p[3][c] - low) / range * (lane - 5);
            if (previous == null) ctx.moveTo(x, a);
            else ctx.lineTo(x, previous);
            ctx.lineTo(x, a); ctx.lineTo(x, b); previous = b;
        }
        ctx.stroke(); ctx.globalAlpha = 1;
    }
    const a = px(start), b = px(end), boxWidth = Math.max(4, b - a);
    const boxLeft = Math.max(left, Math.min(a, right - boxWidth));
    ctx.fillStyle = 'rgba(1,5,12,.60)';
    ctx.fillRect(left, 0, Math.max(0, boxLeft - left), h - 22);
    ctx.fillRect(boxLeft + boxWidth, 0, Math.max(0, right - boxLeft - boxWidth), h - 22);
    ctx.fillStyle = 'rgba(77,214,232,.16)'; ctx.fillRect(boxLeft, 1, boxWidth, h - 24);
    ctx.strokeStyle = '#71e5f4'; ctx.lineWidth = 2; ctx.strokeRect(boxLeft, 1, boxWidth, h - 24); ctx.lineWidth = 1;
    if (playTime >= summary.start && playTime <= summary.end) {
        ctx.strokeStyle = '#64ff88'; ctx.setLineDash([3, 3]); ctx.beginPath();
        ctx.moveTo(px(playTime), 2); ctx.lineTo(px(playTime), h - 23); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.fillStyle = '#afbdd3'; ctx.fillText(fmt(summary.start), left, h - 5);
    const label = fmt(summary.end) + ' ' + summary.axis_unit;
    ctx.fillText(label, Math.max(left, right - ctx.measureText(label).width), h - 5);
    const readout = `Viewing ${(100 * (start - summary.start) / total).toFixed(1)}%-${(100 * (end - summary.start) / total).toFixed(1)}% | ${fmt(start)} to ${fmt(end)} ${summary.axis_unit} | span ${fmt(end - start)} ${summary.axis_unit}`;
    $('#navigatorReadout').textContent = readout;
    const element = $('#waveformNavigator');
    element.setAttribute('aria-valuemin', String(summary.start));
    element.setAttribute('aria-valuemax', String(summary.end));
    element.setAttribute('aria-valuenow', String((start + end) / 2));
    element.setAttribute('aria-valuetext', readout);
    element.dataset.visibleStart = String(start); element.dataset.visibleEnd = String(end);
    $('#navigatorPrevious').disabled = start <= summary.start;
    $('#navigatorNext').disabled = end >= summary.end;
}

function moveNavigator(left, commit = true) {
    if (!summary) return;
    const span = navigatorWindow(left, end - start, summary.start, summary.end);
    if (!span) return;
    pause();
    request++;
    [start, end] = span;
    drawNavigator();
    if (commit) return plot();
}

let navigatorDrag = null;
$('#waveformNavigator').onpointerdown = e => {
    if (!summary) return;
    const element = $('#waveformNavigator'), w = element.getBoundingClientRect().width;
    const time = summary.start + (e.offsetX - 45) / (w - 59) * (summary.end - summary.start);
    navigatorDrag = {x: e.offsetX, first: start, last: end, original: [start, end]};
    if (time < start || time > end) {
        moveNavigator(time - (end - start) / 2, false);
        navigatorDrag.first = start; navigatorDrag.last = end;
    } else { pause(); request++; }
    element.setPointerCapture(e.pointerId);
    element.style.cursor = 'grabbing';
};
$('#waveformNavigator').onpointermove = e => {
    if (!navigatorDrag || !summary) return;
    const w = $('#waveformNavigator').getBoundingClientRect().width;
    const shift = (e.offsetX - navigatorDrag.x) / (w - 59) * (summary.end - summary.start);
    moveNavigator(navigatorDrag.first + shift, false);
};
$('#waveformNavigator').onpointerup = e => {
    if (!navigatorDrag) return;
    navigatorDrag = null;
    $('#waveformNavigator').style.cursor = 'grab';
    if ($('#waveformNavigator').hasPointerCapture(e.pointerId)) $('#waveformNavigator').releasePointerCapture(e.pointerId);
    plot();
};
$('#waveformNavigator').onpointercancel = () => {
    if (navigatorDrag) [start, end] = navigatorDrag.original;
    navigatorDrag = null; $('#waveformNavigator').style.cursor = 'grab'; drawNavigator();
};
$('#waveformNavigator').onkeydown = e => {
    if (!summary) return;
    const span = end - start;
    const targets = {ArrowLeft: start - span * .1, ArrowRight: start + span * .1, PageUp: start - span * .9, PageDown: start + span * .9, Home: summary.start, End: summary.end - span};
    if (!(e.key in targets)) return;
    e.preventDefault(); moveNavigator(targets[e.key]);
};
$('#navigatorPrevious').onclick = () => summary && moveNavigator(start - (end - start) * .8);
$('#navigatorNext').onclick = () => summary && moveNavigator(start + (end - start) * .8);

function renderMinis() {
    if (!summary) return;
    for (const type of ['eye', 'jitter']) {
        const host = type === 'eye' ? 'channelPlots' : 'jitterPlots',
            metrics = (measured || summary).metrics;
        $('#' + host).innerHTML = [0, 1, 2, 3, 4, 5].map(c => {
            const m = metrics.find(m => m.c === c),
                present = summary.present.includes(c),
                traces = view?.eyes[String(c)] || [],
                oos = m?.oos || 0;
            const note = type === 'eye' ? (view?.eye_scope?.[String(c)] || 'Waiting for waveform') : '40 bins · common ±5% scale';
            const total = (m?.hist?.reduce((a, b) => a + b, 0) || 0)+(m?.outside||0);
            const badge = !present ? 'Not recorded' : type === 'eye' ? (c >= 4 ? 'Index pulse' : oos ? oos + ' OOS' : m?.period>0?'Within duty spec':'No complete cycles') : (m?.outside ? m.outside + ' outside scale' : 'No overflow');
            return `<div class="channel-mini" style="--signal:${colors[c]}"><div class="mini-card-head"><span class="mini-signal"><i></i>${names[c]}</span><span class="mini-kind">${type==='eye'?(c>=4?'Index detail':'Eye diagram'):'Period jitter'}</span></div><canvas id="${type}${c}" aria-label="${names[c]} ${type==='eye'?'eye diagram':'period jitter histogram'}"></canvas><div class="mini-card-foot"><span class="mini-note">${present?(type==='eye'?traces.length+' overlays · '+note:fmt(total)+' periods · '+note):'Channel not present'}</span><span class="mini-badge ${!present||(c>=4||!(m?.period>0))&&type==='eye'?'neutral':(type==='eye'?oos:m?.outside)?'bad':''}">${badge}</span></div></div>`;
        }).join('');
        for (const c of [0, 1, 2, 3, 4, 5]) {
            const [ctx, w, h] = canvas(type + c), m = metrics.find(m => m.c === c), traces = view?.eyes[String(c)] || [];
            const left = 38,
                right = w - 12,
                top = 12,
                bottom = h - 30,
                pw = right - left,
                ph = bottom - top;
            ctx.fillStyle = '#091321';
            ctx.fillRect(0, 0, w, h);
            ctx.font = '10px system-ui';
            ctx.strokeStyle = '#1c2d44';
            ctx.lineWidth = 1;
            for (let i = 0; i <= 4; i++) {
                const y = top + ph * i / 4;
                ctx.beginPath();
                ctx.moveTo(left, y);
                ctx.lineTo(right, y);
                ctx.stroke();
            }
            const divisions=type==='eye'&&c>=4?3:4;
            for (let i = 0; i <= divisions; i++) {
                const x = left + pw * i / divisions;
                ctx.beginPath();
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.stroke();
            }
            ctx.fillStyle = '#8fa3bf';
            ctx.textAlign = 'center';
            if (type === 'eye') {
                const vals = traces.flat().map(p => p[1]),
                    min = vals.length ? Math.min(...vals) : 0,
                    max = vals.length ? Math.max(...vals) : 1,
                    range = max - min || 1;
                ctx.textAlign = 'right';
                ctx.fillText(Number(max.toPrecision(3)), left - 6, top + 4);
                ctx.fillText(Number(min.toPrecision(3)), left - 6, bottom + 3);
                ctx.textAlign = 'center';
                for (let i = 0; i <= divisions; i++) ctx.fillText(c>=4?String(i-1):String(i/2),left+pw*i/divisions,bottom+13);
                ctx.fillText(c >= 4 ? 'Reference pulse widths · Z trigger at 0' : 'Pulse periods', left + pw / 2, h - 3);
                if (c >= 4) {
                    ctx.save();
                    ctx.setLineDash([3, 4]);
                    ctx.strokeStyle = '#6a809d';
                    ctx.beginPath();
                    ctx.moveTo(left + pw / 3, top);
                    ctx.lineTo(left + pw / 3, bottom);
                    ctx.stroke();
                    ctx.restore();
                }
                if (!traces.length) {
                    ctx.fillStyle = '#8397b3';
                    ctx.fillText(summary.present.includes(c) ? 'Not enough complete pulse data' : 'Channel not recorded', left + pw / 2, top + ph / 2);
                }
                ctx.save();
                ctx.beginPath();
                ctx.rect(left, top, pw, ph);
                ctx.clip();
                ctx.lineWidth = 1.25;
                for (const [j, trace] of traces.entries()) {
                    ctx.globalAlpha = traces.length <= 3 ? .95 : Math.max(.12, .75 / Math.sqrt(traces.length / 3));
                    ctx.strokeStyle = view?.eye_oos?.[String(c)]?.[j] ? '#ff82b5' : colors[c];
                    ctx.beginPath();
                    trace.forEach(([t, v], i) => {
                        const x = left + t / 2 * pw,
                            y = bottom - 5 - (v - min) / range * (ph - 10);
                        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)
                    });
                    ctx.stroke();
                }
                ctx.restore();
            } else {
                const hist = m?.hist || Array(40).fill(0),
                    max = Math.max(1, ...hist),
                    bw = pw / 40;
                ctx.textAlign = 'right';
                ctx.fillText(fmt(max), left - 6, top + 4);
                ctx.fillText('0', left - 6, bottom + 3);
                const gradient = ctx.createLinearGradient(0, top, 0, bottom);
                gradient.addColorStop(0, colors[c]);
                gradient.addColorStop(1, colors[c] + '55');
                ctx.fillStyle = gradient;
                hist.forEach((v, i) => {
                    if (v) {
                        const bh = v / max * (ph - 5);
                        ctx.fillRect(left + i * bw + .6, bottom - bh, Math.max(1, bw - 1.2), bh)
                    }
                });
                ctx.save();
                ctx.setLineDash([3, 4]);
                ctx.strokeStyle = '#67809d';
                ctx.beginPath();
                ctx.moveTo(left + pw / 2, top);
                ctx.lineTo(left + pw / 2, bottom);
                ctx.stroke();
                ctx.restore();
                ctx.textAlign = 'center';
                ctx.fillStyle = '#8fa3bf';
                for (let i = 0; i <= 4; i++) ctx.fillText((i * 2.5 - 5 > 0 ? '+' : '') + (i * 2.5 - 5) + '%', left + pw * i / 4, bottom + 13);
                ctx.fillText('Period deviation', left + pw / 2, h - 3);
                if (!hist.some(v => v)) {
                    ctx.fillStyle = '#8397b3';
                    ctx.fillText(summary.present.includes(c) ? 'No complete periods' : 'Channel not recorded', left + pw / 2, top + ph / 2);
                }
            }
        }
    }
}
function replayWindow(time, first, last, minimum, maximum) {
    const span = last - first, full = maximum - minimum;
    if (!(span > 0) || span >= full * (1 - 1e-9)) return null;
    const left = Math.max(minimum, Math.min(time - span / 2, maximum - span));
    return [left, left + span];
}

async function followReplayView() {
    if (!playing || !summary || !$('#followReplay').checked) return;
    const window = replayWindow(playTime, start, end, summary.start, summary.end);
    if (!window) return;
    const [left, right] = window;
    if (Math.abs(start - left) <= (end - start) * 1e-9) return;
    start = left;
    end = right;
    await plot();
}

async function position(step, advance, generation = null) {
    if (!summary) return;
    try {
        let d = await api(`/api/position?id=${job}&time=${playTime}&ppr=${encodeURIComponent($('#replayPpr').value)}` + (step != null ? '&step=' + step : '') + (advance != null ? '&advance=' + advance : ''));
        if (generation != null && (generation !== playGeneration || !playing)) return;
        if (!d.available) {
            pause();
            $('#radarStatus').textContent = 'No pulse edges detected. Check thresholds or use Auto threshold.';
            return false;
        }
        playTime = d.time;
        await followReplayView();
        drawReplayCursor();
        $('#radarReadout').textContent = `Azimuth ${d.angle.toFixed(2)}° · North = 0°`;
        $('#radarStatus').textContent = `${d.channel} pulse train · ${fmt(playTime)} ${summary.seconds?'s':'samples'} · ${d.channel==='Z'?'interpolated from Z intervals':d.direction_known?'quadrature direction':'clockwise assumed (single channel)'} · ${fmt(d.ppr)} pulses/rev${d.expected_source === 'pulse timing estimate' ? ' · no Z: expected marker inferred from pulse timing' : ''}${d.ppr_estimated?' (set calibration below)':''} · Loop ${replayLoops+1}${d.at_end&&!playing?' · End of recording':''}`;
        $('#radarPulseDigits').textContent = d.count;
        let [x, w, h] = canvas('radarCanvas'), cx = w / 2, cy = h / 2, r = Math.min(w, h) * .42;
        x.strokeStyle = '#225533';
        x.beginPath();
        x.arc(cx, cy, r, 0, Math.PI * 2);
        x.stroke();
        x.fillStyle = '#50d697';
        x.fillText('N', cx - 4, cy - r - 5);
        for (let [angle, dotted] of [
                [d.expected, true],
                [d.angle, false]
            ]) {
            if (angle == null) continue;
            let a = (angle - 90) * Math.PI / 180;
            x.strokeStyle = dotted ? '#285539' : '#50ff80';
            x.setLineDash(dotted ? [3, 5] : []);
            x.beginPath();
            x.moveTo(cx, cy);
            x.lineTo(cx + r * Math.cos(a), cy + r * Math.sin(a));
            x.stroke()
        }
        x.setLineDash([])
    } catch (e) {
        fail(e)
    }
}

function pause() {
    playing = false;
    playGeneration++;
    clearTimeout(timer)
}
async function play() {
    if (playing || !summary?.replay_available) return;
    if (playTime >= summary.end) playTime = summary.start;
    playing = true;
    const generation = ++playGeneration;
    let last = performance.now();
    async function tick() {
        if (!playing || generation !== playGeneration) return;
        let now = performance.now(),
            elapsed = (now - last) / 1000;
        last = now;
        const speed = Math.max(0.1, Math.min(1, Number($('#radarSpeed').value) || 1));
        await position(null, elapsed * speed, generation);
        if (!playing || generation !== playGeneration) return;
        if (playing && playTime >= summary.end) {
            replayLoops++;
            playTime = summary.start;
            await position(null, null, generation);
        }
        if (playing && generation === playGeneration) timer = setTimeout(tick, 50)
    }
    tick()
}
function syncRecordingFormat() {
    const binary = $('#recordingFormat').value === 'bin';
    $('#csvInputs').hidden = $('#recordingFormat').value !== 'csv';
    $('#csvChannelInputs').hidden = $('#recordingFormat').value !== 'csv-channels';
    $('#binInputs').hidden = !binary;
}
$('#recordingFormat').onchange = syncRecordingFormat;
syncRecordingFormat();
for (let i=0;i<6;i++) {
    $('#browseBin'+i).onclick = async () => {
        try {
            const d = await api('/api/browse', {kind:'bin'});
            if (d.path) $('#binPath'+i).value = d.path;
        } catch(e) { fail(e) }
    };
    $('#clearBin'+i).onclick = () => { $('#binPath'+i).value = ''; };
}

for (let i=0;i<6;i++) {
    $('#browseCsvChannel'+i).onclick = async () => {
        try {
            const d=await api('/api/browse',{kind:'csv-channel'});
            if(d.path) $('#csvChannelPath'+i).value=d.path;
        } catch(e) {fail(e)}
    };
    $('#clearCsvChannel'+i).onclick=()=>{$('#csvChannelPath'+i).value='';};
}
$('#loadCsvChannels').onclick=async()=>{
    if(pendingAnalysis || loadingTask || deletingCache) return;
    const channels={};
    ['A','-A','B','-B','Z','-Z'].forEach((name,i)=>{
        const path=$('#csvChannelPath'+i).value.trim();
        if(path) channels[name]=path;
    });
    if(!Object.keys(channels).length){status('Choose at least one CSV channel');return;}
    csvChannelLoading=true;
    $('#csvChannelError').hidden=true;
    $('#csvChannelError').textContent='';
    pause();
    showLoading('analysis','Loading CSV channels','Checking sample timelines and analysing every sample...');
    try {
        const d=await api('/api/open-csv-channels',{channels,options:options()});
        job=d.id;pendingAnalysis=true;poll();
    } catch(e){fail(e)}
};

$('#loadBin').onclick = async () => {
    if (pendingAnalysis || loadingTask || deletingCache) return;
    const channels = {};
    ['A','-A','B','-B','Z','-Z'].forEach((name,i) => {
        const path=$('#binPath'+i).value.trim();
        if (path) channels[name]=path;
    });
    if (!Object.keys(channels).length) {status('Choose at least one BIN channel');return;}
    binaryLoading = true;
    $('#binError').hidden = true;
    $('#binError').textContent = '';
    pause();
    showLoading('analysis','Loading BIN channels','Reading BXM metadata and analysing every sample...');
    try {
        const d=await api('/api/open-bin',{channels,options:{...options(),bin_align_90:$('#binAlign90').checked}});
        job=d.id;pendingAnalysis=true;poll();
    } catch(e) {fail(e)}
};
$('#browsePath').onclick = async () => {
    try {
        status('Choose a recording in the file browser');
        const d = await api('/api/browse', {});
        if (d.path) {
            $('#localPath').value = d.path;
            await open(d.path)
        } else status('File selection cancelled')
    } catch (e) {
        fail(e)
    }
};
for (const role of ['A', 'B', 'C']) {
    $('#browseMerge' + role).onclick = async () => {
        try {
            const d = await api('/api/browse', {});
            if (d.path) $('#mergePath' + role).value = d.path;
        } catch (e) { fail(e) }
    };
}
$('#clearMergeC').onclick = () => { $('#mergePathC').value = ''; };
function finishAlignmentChoice(mode) {
    const resolve = alignmentChoiceResolve;
    if (!resolve) return;
    alignmentChoiceResolve = null;
    $('#alignmentChoiceDialog').close();
    resolve(mode);
}
function chooseAlignmentMode() {
    return new Promise(resolve => {
        alignmentChoiceResolve = resolve;
        $('#alignmentChoiceDialog').showModal();
    });
}
$('#alignmentCheckOnly').onclick = () => finishAlignmentChoice('check');
$('#alignmentRefineB').onclick = () => finishAlignmentChoice('refine');
$('#alignmentChoiceCancel').onclick = () => finishAlignmentChoice(null);
$('#alignmentChoiceDialog').addEventListener('cancel', e => {
    e.preventDefault(); finishAlignmentChoice(null);
});
$('#alignmentChoiceDialog').addEventListener('close', () => finishAlignmentChoice(null));
$('#mergeCaptures').onclick = async () => {
    if (pendingAnalysis || mergeSubmissionPending || deletingCache || loadingTask) return;
    const path_a = $('#mergePathA').value.trim(), path_b = $('#mergePathB').value.trim(), path_c = $('#mergePathC').value.trim() || null;
    if (!path_a || !path_b) { status('Choose both four-channel captures before merging'); return; }
    const selectedOptions = options();
    mergeSubmissionPending = true;
    $('#mergeCaptures').disabled = true;
    pause();
    try {
        const alignment_mode = path_c ? await chooseAlignmentMode() : 'check';
        if (alignment_mode == null) { status('Merge cancelled'); return; }
        showLoading('analysis', 'Merging captures', alignment_mode === 'refine' ? 'Aligning Z pulses, then refining B timing from the A/B reference...' : 'Importing captures, aligning Z pulses and checking A/B phase...');
        const d = await api('/api/merge', {path_a, path_b, path_c, options: {...selectedOptions, alignment_mode}});
        job = d.id;
        pendingAnalysis = true;
        poll();
    } catch (e) { fail(e) }
    finally { mergeSubmissionPending = false; $('#mergeCaptures').disabled = false; }
};
$('#cancelJob').onclick = () => job && api('/api/cancel', {
    id: job
}).catch(fail);
$('#expectedPprEnabled').onchange = () => {
    if ($('#expectedPprEnabled').checked && !$('#expectedPpr').value) $('#expectedPpr').value = summary?.diagnostics.reference_ppr || 8192;
    $('#expectedPpr').disabled = !$('#expectedPprEnabled').checked;
    $('#expectedPprStatus').textContent = $('#expectedPprEnabled').checked ? 'Entered reference enabled' : 'Automatic modal reference';
    scheduleReanalysis()
};
async function reanalyse(auto = false, nextOptions = null, modal = false) {
    if (!summary) return;
    if (pendingAnalysis) {
        status('Analysis is running · apply further changes when complete');
        return;
    }
    pause();
    if (modal) showLoading('analysis', auto ? 'Finding thresholds' : 'Analysing recording', 'Processing the complete recording...');
    pendingAnalysis = true;
    syncCacheControls();
    try {
        let d = await api('/api/reanalyse', {
            id: job,
            options: {
                ...(nextOptions || options()),
                auto
            }
        });
        job = d.id;
        pendingAnalysis = true;
        poll()
    } catch (e) {
        fail(e)
    }
}
$('#autoThreshold').onclick = () => summary ? reanalyse(true, null, true) : ($('#localPath').value ? open($('#localPath').value, true) : status('Select a recording first'));

function scheduleReanalysis() {
    if (pendingAnalysis) {
        queuedOptions = options();
        status("Settings queued for next analysis");
        return;
    }
    clearTimeout(editTimer);
    status('Settings changed · recalculating whole recording');
    editTimer = setTimeout(() => reanalyse(), 450);
}
for (let id of ['lowThreshold', 'highThreshold', 'smoothWindow', 'expectedPpr', 'timecol', 'delimiter', 'smoothEdges', 'slewMode', 'receiverMode', 'smoothingMode', 'edgeMode']) $('#' + id).onchange = () => {
    if(id==='smoothEdges'){syncRawPlotMode();draw();}
    $('#smoothWindowValue').textContent = $('#smoothWindow').value;
    scheduleReanalysis();
};
$('#smoothEdges').parentElement.title = 'Adaptive majority cleanup before measurement, matching online defaults';
$('#measurementScope').onchange = () => plot();
$('#applyspan').onclick = () => {
    start = +$('#tstart').value;
    end = +$('#tend').value;
    plot()
};
for (let id of ['fitzoom', 'fullspan']) $('#' + id).onclick = () => {
    if (summary) {
        start = summary.start;
        end = summary.end;
        plot()
    }
};
$('#trimBeforeFirstZ').onchange = () => {
    if (!summary) return;
    if ($('#trimBeforeFirstZ').checked) {
        preTrimSpan = [start, end];
        start = summary.first_z ?? summary.start;
    } else if (preTrimSpan) {
        [start, end] = preTrimSpan;
        preTrimSpan = null;
    }
    plot();
};

function showFaultDetails() {
    if (!selectedFault) {
        $('#errorNavStatus').textContent = `${view?.faults?.count||0} errors in view · click Zoom to error / Next`;
        return
    }
    const e = selectedFault;
    if(e.kind==='ambiguous quadrature transition' || e.kind==='short raw pulse candidate'){
        $('#errorNavStatus').textContent=`${e.channels?'A/B':e.channel} · ${e.kind} · ${eng(e.time,summary?.axis_unit || 'samples')}${e.width!=null?' · width '+eng(e.width,summary?.axis_unit || 'samples')+'; limit '+eng(e.width_limit,summary?.axis_unit || 'samples'):''}`;return;
    }
    if(e.kind==='A/B phase outlier'){
        const text=`A/B · cycle ${e.pulse} · phase ${fmt(e.phase_degrees)}° · typical ${fmt(e.typical_degrees)}° · deviation ${fmt(e.deviation_degrees)}° (tolerance ${fmt(e.tolerance_degrees)}°)`;
        $('#errorNavStatus').textContent=text;$('#phaseOutlierDetails').textContent=text;return;
    }
    $('#errorNavStatus').textContent = `${e.channel} · ${e.kind} · pulse ${e.pulse} · ${e.duty!=null?e.duty.toFixed(2)+'% duty (allowed 45–55%)':(e.missing?e.missing+' missing; ':'')+'observed '+e.observed+', expected '+e.expected+' (Z-to-Z interval)'}`;
}
async function focusFault(e) {
    if (!e) {
        selectedFault = null;
        $('#errorNavStatus').textContent = 'No errors found in the recording';
        return
    }
    selectedFault = e;
    $('#hideErrorMarkers').checked = false;
    const channel = e.channel.replace('Rx ', '').replace('−', '-');
    const metrics = summary.metrics.find(m => m.channel === channel && m.period > 0) || summary.metrics.find(m => [6, 7].includes(m.c) && m.period > 0);
    const period = metrics?.period || ((summary.end - summary.start) / 16);
    const center = (e.time + (e.end_time ?? e.time)) / 2;
    start = e.zoom_start ?? Math.max(summary.start, Math.min(center - 8 * period, summary.end - 16 * period));
    end = e.zoom_end ?? Math.min(summary.end, start + 16 * period);
    await plot();
    showFaultDetails();
}
$('#zoomErrors').onclick = async () => {
    if (!summary) return;
    try {
        const q = selectedFault ? `&after_time=${selectedFault.time}&after_id=${selectedFault.id}` : '';
        const hiddenPhase=$('#hidePhaseFaults').checked?'&exclude_kind='+encodeURIComponent('A/B phase outlier'):'';
        const d = await api('/api/next-fault?id=' + job + q + hiddenPhase);
        await focusFault(d.event);
    } catch (e) {
        fail(e)
    }
};
$('#xzoom').oninput = () => {
    if (summary) {
        let factor = 10 ** (+$('#xzoom').value / 25),
            mid = (start + end) / 2,
            span = (summary.end - summary.start) / factor;
        start = mid - span / 2;
        end = mid + span / 2;
        $('#zoomreadout').textContent = factor.toFixed(1) + '×';
        plot()
    }
};
$('#rawPlot').onchange = draw;
$('#hideErrorMarkers').onchange = draw;
$('#hidePhaseFaults').onchange=()=>{
    if($('#hidePhaseFaults').checked && selectedFault?.kind==='A/B phase outlier'){
        selectedFault=null;$('#phaseOutlierDetails').textContent='';showFaultDetails();
    }
    $('#nextPhaseOutlier').disabled=$('#hidePhaseFaults').checked || !summary?.phase_analysis?.outliers;
    draw();
};
$('#phaseTolerance').oninput=()=>{$('#phaseToleranceValue').textContent=$('#phaseTolerance').value+'%';};
$('#phaseTolerance').onchange=()=>{if(summary)scheduleReanalysis();};
for (let id of ['chart', 'rxChart']) {
    let c = $('#' + id),
        down = null;
    const hideSelection = () => { $('#zoomSelection').hidden = true; };
    const selectionX = e => {
        const rect = c.getBoundingClientRect();
        return Math.max(45, Math.min(rect.width - 10, e.clientX - rect.left));
    };
    c.onpointerdown = e => {
        if (!summary || e.button !== 0) return;
        pause();
        down = selectionX(e);
        hideSelection();
        c.setPointerCapture(e.pointerId);
    };
    c.onpointermove = e => {
        if (down == null) return;
        const rect = c.getBoundingClientRect(), current = selectionX(e), box = $('#zoomSelection');
        box.hidden = Math.abs(current - down) <= 5;
        Object.assign(box.style, {
            left: (rect.left + Math.min(down, current)) + 'px',
            top: (rect.top + 15) + 'px',
            width: Math.abs(current - down) + 'px',
            height: Math.max(0, rect.height - 35) + 'px'
        });
    };
    c.onpointercancel = c.onlostpointercapture = () => { down = null; hideSelection(); };
    c.onpointerup = e => {
        if (!summary || down == null) return;
        const initial = down, current = selectionX(e);
        down = null;
        hideSelection();
        if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
        let w = c.getBoundingClientRect().width,
            at = x => start + (x - 45) / (w - 55) * (end - start),
            a = at(initial),
            b = at(current);
        if (Math.abs(current - initial) > 5) {
            start = Math.min(a, b);
            end = Math.max(a, b);
            plot()
        } else {
            let nearest = (view.faults?.events || []).find(v => Math.abs(v.time - a) < (end - start) * .015);
            if (nearest) {
                focusFault(nearest);
            }
        }
    };
    c.onwheel = e => {
        if (!summary) return;
        e.preventDefault();
        let shift = Math.sign(e.deltaY) * (end - start) * .1;
        start += shift;
        end += shift;
        if (start < summary.start) {
            end += summary.start - start;
            start = summary.start
        }
        if (end > summary.end) {
            start -= end - summary.end;
            end = summary.end
        }
        plot()
    };
}
$('#radarPlay').onclick = play;
$('#radarPause').onclick = pause;
for (let [id, n] of [
        ['Back1', -1],
        ['Back10', -10],
        ['Back20', -20],
        ['Step1', 1],
        ['Step10', 10],
        ['Step20', 20]
    ]) $('#radar' + id).onclick = () => {
    pause();
    position(n)
};
$('#radarSpeed').oninput = () => {
    const speed = +$('#radarSpeed').value;
    $('#radarSpeedValue').textContent = speed.toFixed(1) + '× (' + Math.round(speed * 100) + '%)'
};
for (let [id, kind] of [
        ['exportCsv', 'csv'],
        ['exportPdf', 'pdf']
    ]) $('#' + id).onclick = async () => {
    if (!summary) return;
    showLoading('export', 'Preparing ' + kind.toUpperCase() + ' download', 'Preparing the full recording for export...');
    try {
        const d = await api('/api/prepare-export', {
            id: job,
            kind,
            serial: $('#serialNumber').value,
            csv_mode: 'original'
        });
        for (;;) {
            const state = await api(`/api/export-status?id=${job}&export=${d.export}`);
            if (state.error) throw Error(state.error);
            if (state.ready) break;
            await new Promise(resolve => setTimeout(resolve, 350));
        }
        const a = document.createElement('a');
        a.href = `/api/export-file?id=${job}&export=${d.export}`;
        a.download = 'encoder-analysis.' + kind;
        a.click();
        hideLoading('export');
        status(kind.toUpperCase() + ' download ready');
        refreshCache();
    } catch (e) {
        fail(e)
    }
};
$('#copyrightYear').textContent = new Date().getFullYear();
window.onresize = () => {
    draw();
    renderMinis();
    position()
};
api('/api/session').then(d => {
    token = d.token;
    refreshCache(true);
    try {
        const notice = window.sessionStorage?.getItem('encoder-cache-notice');
        if (notice) { status(notice); window.sessionStorage.removeItem('encoder-cache-notice'); }
    } catch (_) {}
}).catch(fail);
$('#rpm').onchange = async () => {
    if (!summary) return;
    try {
        const d = await api('/api/rpm', {
            id: job,
            rpm: $('#rpm').value
        });
        Object.assign(summary, d);
        await updateMeasurements(request);
        renderMinis();
        status('RPM updated');
        await position()
    } catch (e) {
        fail(e)
    }
};
let rpmEditTimer;
$('#rpm').oninput = () => {
    clearTimeout(rpmEditTimer);
    rpmEditTimer = setTimeout(() => $('#rpm').onchange(), 250)
};
$('#localPath').onkeydown = e => {
    if (e.key === 'Enter') open($('#localPath').value)
};
$('#replayPpr').onchange = () => {
    pause();
    position()
};
for (const [id, kind] of [
        ['testData', 'clean'],
        ['testMissingData', 'missing'],
        ['testJitterData', 'jitter'],
        ['testBearingData', 'bearing']
    ]) $('#' + id).onclick = async () => {
    if(pendingAnalysis||loadingTask||deletingCache)return;
    pause();
    try {
        showLoading('analysis', 'Generating test data', 'Creating the ' + kind + ' recording...');
        status('Generating reproducible ' + kind + ' data');
        const d = await api('/api/generate', {
            kind,
            differential: $('#testIncludeDifferential').checked,
            ...(kind==='bearing'?{}:{seed:Number($('#testSeed').value)}),
            options: options()
        });
        job = d.id;
        pendingAnalysis = true;
        $('#rpm').value = 60;
        if(kind==='bearing' && d.generated)$('#testSeed').value=d.generated.seed;
        $('#expectedPprEnabled').checked = ['missing','bearing'].includes(kind);
        $('#expectedPpr').disabled = !['missing','bearing'].includes(kind);
        $('#expectedPpr').value = 8192;
        poll()
    } catch (e) {
        fail(e)
    }
};

syncRawPlotMode();

const radarJokes=[
    "I asked the radar where my motivation had gone. It said: ‘No returns.’",
    "A and B started a band. Great timing. Terrible phase relationship.",
    "The antenna and I have a lot in common. We’ve both spent all day going round in circles, but only one of us gets called precision equipment.",
    "Fault report: antenna spinning normally. Engineer spinning a better story.",
    "I finally achieved a perfect 90-degree phase relationship. Then someone moved the oscilloscope probe."
];
let radarJokeIndex=Math.floor(Math.random()*radarJokes.length);
$('#comedyButton').onclick=()=>{
    $('#comedyJoke').textContent=radarJokes[radarJokeIndex++%radarJokes.length];
    if(!$('#comedyDialog').open)$('#comedyDialog').showModal();
};
$('#closeComedy').onclick=()=>$('#comedyDialog').close();
