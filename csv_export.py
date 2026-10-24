"""Editable CSV export headings with bounded-memory, source-preserving writes."""
import csv
import io
import shutil
from pathlib import Path
from engine import NAMES


def layout(r):
    source = (r.source.suffix.lower() not in ('.wav','.bin')
              and not r.options.get('csv_channel_paths')
              and not getattr(r,'merge_info',None))
    return dict(headers=list(r.headers) if source else [r.time_heading]+NAMES,
                source_layout=source,
                note=('Saves the original column order, numeric values and time units; only the headings change.' if source
                      else 'Saves the combined decoded recording: time followed by A, -A, B, -B, Z, -Z. Unavailable channels contain zero.'))


def validate_headers(headers, count):
    if not isinstance(headers,list) or len(headers)!=count:
        raise ValueError(f'Provide exactly {count} CSV column names')
    if any(not isinstance(v,str) or not v.strip() or len(v)>128 or any(ord(c)<32 or ord(c)==127 for c in v) for v in headers):
        raise ValueError('Column names must be non-empty text, at most 128 characters, without control characters')
    headers=[v.strip() for v in headers]
    if len({v.casefold() for v in headers})!=len(headers):
        raise ValueError('Each CSV column name must be unique')
    return headers


def write_csv(r,target,headers):
    import numpy as np
    from engine import BLOCK
    meta=layout(r)
    headers=validate_headers(headers,len(meta['headers']))
    try:
        if meta['source_layout']:
            with r.source.open('rb') as source:
                first=source.readline(1024*1024+1)
                if len(first)>1024*1024:raise ValueError('CSV header exceeds 1 MiB')
                delimiter=getattr(r,'csv_delimiter',None)
                if delimiter is None:
                    option=r.options.get('delimiter','auto')
                    delimiter=max([',',';','\t'],key=first.decode('utf-8-sig').count) if option=='auto' else ('\t' if option=='\\t' else option)
                if getattr(r,'csv_headerless',getattr(r,'csv_columns',{}).get('headerless',False)):
                    source.seek(0)
                    if source.read(3)!=b'\xef\xbb\xbf':source.seek(0)
                buffer=io.StringIO(newline='')
                csv.writer(buffer,delimiter=delimiter,lineterminator='\n').writerow(headers)
                with Path(target).open('wb') as dest:
                    dest.write(buffer.getvalue().encode('utf-8'))
                    shutil.copyfileobj(source,dest,4*1024*1024)
        else:
            with Path(target).open('w',encoding='utf-8',newline='') as dest:
                csv.writer(dest,lineterminator='\n').writerow(headers)
                for first in range(0,r.count,BLOCK):
                    np.savetxt(dest,r.data[first:first+BLOCK],delimiter=',',fmt='%.17g')
    except Exception:
        Path(target).unlink(missing_ok=True)
        raise
