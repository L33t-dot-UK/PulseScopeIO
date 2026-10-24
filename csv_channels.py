"""Combine independently selected single-channel CSV files on a verified timeline."""
import shutil
import numpy as np
from pathlib import Path


def ingest(recording):
    from engine import Recording, NAMES, MAX_BYTES, BLOCK
    from cache_control import CacheManager
    paths=recording.options['csv_channel_paths']
    if not isinstance(paths,dict) or not paths or len(paths)>6 or any(k not in NAMES or not isinstance(v,str) for k,v in paths.items()):
        raise ValueError('Select one to six named single-channel CSV files')
    selected=[(NAMES.index(k),Path(v).resolve()) for k,v in paths.items()]
    if any(not p.is_file() or p.suffix.lower() not in ('.csv','.tsv','.txt') for _,p in selected):
        raise ValueError('Choose existing CSV, TSV or text channel files')
    if len({p for _,p in selected})!=len(selected):
        raise ValueError('Each channel must use a different CSV file')
    if sum(p.stat().st_size for _,p in selected)>MAX_BYTES:
        raise ValueError('Maximum combined single-channel CSV source size is 10 GiB')
    children=[]
    sizes=[p.stat().st_size for _,p in selected]
    total_bytes=max(sum(sizes),1)
    imported_bytes=0
    try:
        for index,(c,p) in enumerate(selected):
            recording.check()
            options={k:v for k,v in recording.options.items() if k not in ('csv_channel_paths','binary_paths','capture_role')}
            options['single_csv_channel']=c
            child=Recording(p,options);children.append((c,child))
            base_progress=.32*imported_bytes/total_bytes
            weight=.32*sizes[index]/total_bytes
            last_progress=[None]
            def report_import(child=child,base=base_progress,weight=weight,last=last_progress):
                recording.check()
                if child.progress != last[0]:
                    last[0]=child.progress
                    recording.progress=base+weight*min(1.,max(0.,child.progress/.4))
            child.check=report_import
            recording.stage=f'Reading CSV channel {NAMES[c]} ({index+1} of {len(selected)})'
            report_import()
            child.ingest()
            imported_bytes+=sizes[index]
            recording.progress=.32*imported_bytes/total_bytes
        ref=children[0][1]
        for _,child in children:
            if child.count!=ref.count or child.has_time!=ref.has_time or child.seconds!=ref.seconds or (not ref.seconds and child.source_time_unit!=ref.source_time_unit):
                raise ValueError('Single-channel CSV files must have matching sample counts and time units')
        recording.stage='Checking timelines and combining CSV channels'
        with (recording.dir/'samples.bin').open('wb') as out:
            for k in range(0,ref.count,BLOCK):
                recording.check()
                n=min(BLOCK,ref.count-k)
                times=ref.data[k:k+n,0]
                data=np.zeros((n,7),dtype='<f8');data[:,0]=times
                for c,child in children:
                    if not np.allclose(child.data[k:k+n,0],times,rtol=0,atol=max(1e-12,abs(float(times[-1]-times[0]))*1e-10)):
                        raise ValueError('Single-channel CSV time bases do not match; use files from the same capture and matching time columns')
                    data[:,c+1]=child.data[k:k+n,c+1]
                data.tofile(out);recording.progress=.32+.08*(k+n)/ref.count
        for key in ('count','seconds','has_time','source_time_unit','time_heading','start','end'):
            setattr(recording,key,getattr(ref,key))
        assumptions=[dict(channel=NAMES[c],**child.csv_columns) for c,child in children if hasattr(child,'csv_columns')]
        if assumptions:recording.csv_columns=dict(assumed=True,channels=assumptions,note='Some channel CSV layouts were inferred; the selected channel rows determine signal assignments. First columns are treated as time and unlabelled time units as seconds. Check your capture layout.')
        recording.present=sorted(c for c,_ in children)
        recording.headers=[ref.time_heading]+[NAMES[c] for c in recording.present]
        recording.display_name='CSV channels: '+', '.join(NAMES[c] for c in recording.present)
        recording.data=np.memmap(recording.dir/'samples.bin',dtype='<f8',mode='r',shape=(recording.count,7))
    finally:
        CacheManager.close_recordings([child for _,child in children])
        for _,child in children:
            assert child.dir.resolve().parent==recording.dir.resolve().parent
            shutil.rmtree(child.dir)
