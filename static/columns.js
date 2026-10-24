'use strict';
let csvEditorId=null,csvEditorOriginal=[],csvEditorInputs=[],csvEditorBusy=false;
function csvColumnValidation(){
    const names=csvEditorInputs.map(input=>input.value.trim());
    if(names.some(name=>!name||name.length>128||/[\x00-\x1f\x7f]/.test(name)))return 'Use non-empty column names up to 128 characters, without control characters.';
    if(new Set(names.map(name=>name.toLowerCase())).size!==names.length)return 'Each column name must be unique.';
    return '';
}
function syncCsvColumnSave(){
    const error=csvColumnValidation(),changed=csvEditorInputs.some((input,i)=>input.value.trim()!==csvEditorOriginal[i]);
    $('#saveRenamedCsv').disabled=csvEditorBusy||!!error||!changed;
    if(!csvEditorBusy){
        $('#csvRenameStatus').textContent=error||(changed?'Ready to save a new CSV with these column names.':'Edit column names to enable Save.');
        $('#csvRenameStatus').style.color=error?'#ff9aab':'';
    }
}
function renderCsvColumns(recording){
    const meta=recording?.csv_export_columns;
    $('#csvRenamePanel').hidden=!meta;
    if(!meta){csvEditorId=null;csvEditorInputs=[];csvEditorOriginal=[];$('#csvRenameFields').replaceChildren();$('#saveRenamedCsv').disabled=true;return;}
    if(csvEditorId===recording.id)return;
    csvEditorId=recording.id;csvEditorOriginal=[...meta.headers];csvEditorInputs=[];
    $('#csvRenameNote').textContent=meta.note;
    $('#csvRenameFields').replaceChildren();
    meta.headers.forEach((name,i)=>{
        const label=document.createElement('label'),caption=document.createElement('span'),input=document.createElement('input');
        caption.textContent=`Column ${i+1} · ${name||'Unlabelled'}`;
        input.type='text';input.value=name;input.maxLength=128;input.setAttribute('aria-label','CSV column '+(i+1)+' name');
        input.oninput=syncCsvColumnSave;label.append(caption,input);$('#csvRenameFields').append(label);csvEditorInputs.push(input);
    });
    syncCsvColumnSave();
}
$('#saveRenamedCsv').onclick=async()=>{
    if(!summary||csvEditorBusy||pendingAnalysis||loadingTask||deletingCache||job!==csvEditorId)return;
    const error=csvColumnValidation();
    if(error){$('#csvRenameStatus').textContent=error;return;}
    const id=job,headers=csvEditorInputs.map(input=>input.value.trim());
    csvEditorBusy=true;$('#saveRenamedCsv').disabled=true;csvEditorInputs.forEach(input=>input.disabled=true);
    showLoading('export','Saving renamed CSV','Preparing a new CSV with your column names...');
    try{
        const d=await api('/api/prepare-export',{id,kind:'csv',csv_headers:headers});
        for(;;){
            const state=await api(`/api/export-status?id=${id}&export=${d.export}`);
            if(state.error)throw Error(state.error);
            if(state.ready)break;
            await new Promise(resolve=>setTimeout(resolve,350));
        }
        const link=document.createElement('a');link.href=`/api/export-file?id=${id}&export=${d.export}`;
        link.download=summary.merge_info?.refinement?.applied?'encoder-renamed-refined-B.csv':'encoder-renamed.csv';link.click();
        hideLoading('export');status('Renamed CSV download ready');refreshCache();
        $('#csvRenameStatus').textContent='New CSV download ready. The original file is unchanged.';
    }catch(e){$('#csvRenameStatus').textContent=e.message;$('#csvRenameStatus').style.color='#ff9aab';fail(e)}
    finally{csvEditorBusy=false;csvEditorInputs.forEach(input=>input.disabled=false);$('#saveRenamedCsv').disabled=!!csvColumnValidation();}
};
if(summary)renderCsvColumns(summary);
