import json, os, datetime, xml.etree.ElementTree as ET
from pathlib import Path
import requests, feedparser

OUT=Path('reports'); OUT.mkdir(exist_ok=True)
report={}

def dart():
    key=os.environ['DART_API_KEY']; today=datetime.datetime.now().strftime('%Y%m%d')
    r=requests.get('https://opendart.fss.or.kr/api/list.json',params={'crtfc_key':key,'bgn_de':today,'end_de':today,'page_count':1},timeout=30)
    r.raise_for_status(); j=r.json(); status=str(j.get('status',''))
    ok=status in {'000','013'}
    return {'ok':ok,'status':status,'message':j.get('message'),'total_count':j.get('total_count')}

def kis():
    appkey=os.environ.get('KIS_APP_KEY'); secret=os.environ.get('KIS_APP_SECRET')
    if not appkey or not secret: return {'ok':False,'error':'KIS_APP_KEY/KIS_APP_SECRET missing'}
    base='https://openapi.koreainvestment.com:9443'
    tr=requests.post(base+'/oauth2/tokenP',json={'grant_type':'client_credentials','appkey':appkey,'appsecret':secret},headers={'content-type':'application/json'},timeout=30)
    tr.raise_for_status(); token=tr.json().get('access_token')
    if not token: return {'ok':False,'error':'no access_token'}
    h={'authorization':f'Bearer {token}','appkey':appkey,'appsecret':secret,'tr_id':'FHKST01010100','custtype':'P'}
    q=requests.get(base+'/uapi/domestic-stock/v1/quotations/inquire-price',headers=h,params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'005930'},timeout=30)
    q.raise_for_status(); j=q.json(); price=(j.get('output') or {}).get('stck_prpr')
    return {'ok':str(j.get('rt_cd'))=='0' and bool(price),'rt_cd':j.get('rt_cd'),'msg':j.get('msg1'),'symbol':'005930','price':price}

def rss():
    url='https://nvidianews.nvidia.com/cats/press_release.xml'
    r=requests.get(url,headers={'User-Agent':'AI-Newsroom-RC/1.0'},timeout=30); r.raise_for_status()
    f=feedparser.loads(r.content)
    return {'ok':len(f.entries)>0,'url':url,'entries':len(f.entries),'latest_title':f.entries[0].get('title') if f.entries else None}

for name,fn in [('opendart',dart),('kis',kis),('nvidia_rss',rss)]:
    try: report[name]=fn()
    except Exception as e: report[name]={'ok':False,'error':str(e)[:700]}
(OUT/'live_data_gate.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
if not all(v.get('ok') for v in report.values()): raise SystemExit('Live data gate failed')
