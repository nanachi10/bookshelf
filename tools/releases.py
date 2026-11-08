"""Incrementally collect public numbered books (NDL OAI-PMH); no shelf input."""
import datetime as dt
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

dest = Path(__file__).resolve().parents[1] / 'releases.json'
old = json.loads(dest.read_text('utf8')) if dest.exists() else {}
rows = {r[0]: r for r in old.get('rows', [])}
today = dt.datetime.now(dt.timezone.utc).date()
end = today - dt.timedelta(days=1)
start = old.get('through', (end-dt.timedelta(days=6)).isoformat())
params = dict(verb='ListRecords', metadataPrefix='dcndl', set='iss-ndl-opac',
              **{'from':start, 'until':end.isoformat()})
while True:
    url = 'https://ndlsearch.ndl.go.jp/api/oaipmh?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=90) as response:
        doc = ET.parse(response)
    err = doc.find('{*}error')
    if err is not None:
        if err.get('code')=='noRecordsMatch': break
        raise ValueError(err.text)
    if doc.find('{*}ListRecords') is None: raise ValueError('missing record list')
    for it in doc.findall('.//{*}BibResource'):
        vol = it.findtext('{*}volume/{*}Description/{*}value','')
        if not re.fullmatch(r'[0-9０-９]{1,3}',vol) or not 1<=int(vol)<=200: continue
        title = it.findtext('{http://purl.org/dc/elements/1.1/}title/{*}Description/{*}value','')
        author = re.sub(r',\s*\d{4}-.*','',it.findtext('{*}creator/{*}Agent/{*}name','')).replace(', ','')
        pub = it.findtext('{*}publisher/{*}Agent/{*}name','')
        date = it.findtext('{*}issued','')
        if not (title and author and pub and date[:4].isdigit()) or date[:4]<'2024': continue
        if it.findtext('{*}publicationPlace','JP')!='JP': continue
        for part in it.findall('{*}identifier'):
            if not any(v.endswith('/ISBN') for v in part.attrib.values()): continue
            isbn = (part.text or '').replace('-','')
            if re.fullmatch(r'97[89]\d{10}',isbn):
                rows[isbn]=[isbn,title,int(vol),author,pub,date]
    token = doc.findtext('.//{*}resumptionToken')
    if not token: break
    params = dict(verb='ListRecords',resumptionToken=token)
    time.sleep(1)
dest.write_text(json.dumps(dict(through=end.isoformat(),rows=sorted(rows.values())),
                ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf8')
print(len(rows),'records through',end)
