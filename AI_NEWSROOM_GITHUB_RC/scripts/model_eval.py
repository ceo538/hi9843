import json, os, re, time
from pathlib import Path
import requests

OUT=Path('reports'); OUT.mkdir(exist_ok=True)
CASES=[
 {"id":"direct","prompt":"Known evidence: An official supplier announcement states that Company A directly supplies power modules to Company B. Classify the A-B relation. Return ONLY JSON with keys relationship_status, directness, missing_link, reason. relationship_status must be one of VERIFIED, INFERRED, UNVERIFIED.","expected":"VERIFIED"},
 {"id":"indirect","prompt":"Known evidence: NVIDIA supplies GPUs to ServerCo. Company A supplies power modules to ServerCo. There is no evidence that Company A supplies NVIDIA directly. Classify the relationship between Company A and NVIDIA. Return ONLY JSON with keys relationship_status, directness, missing_link, reason. relationship_status must be one of VERIFIED, INFERRED, UNVERIFIED.","expected_not":"VERIFIED"},
 {"id":"unknown","prompt":"A news article mentions Company A and NVIDIA in the same paragraph but provides no contract, customer, product, or supply-chain evidence connecting them. Classify the relationship. Return ONLY JSON with keys relationship_status, directness, missing_link, reason. relationship_status must be one of VERIFIED, INFERRED, UNVERIFIED.","expected_not":"VERIFIED"},
]

def extract_json(s):
    s=s.strip(); s=re.sub(r'^```(?:json)?','',s); s=re.sub(r'```$','',s).strip()
    a=s.find('{'); b=s.rfind('}')
    if a<0 or b<a: raise ValueError('no json object')
    return json.loads(s[a:b+1])

def openai(prompt):
    key=os.environ['OPENAI_API_KEY']; model=os.getenv('OPENAI_MODEL','gpt-5.6-sol')
    r=requests.post('https://api.openai.com/v1/responses',headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json={'model':model,'input':prompt,'max_output_tokens':350},timeout=90)
    r.raise_for_status(); j=r.json(); parts=[]
    for item in j.get('output',[]):
        for c in item.get('content',[]):
            if isinstance(c,dict) and c.get('text'): parts.append(c['text'])
    if not parts and j.get('output_text'): parts=[j['output_text']]
    return model,'\n'.join(parts)

def anthropic(prompt):
    key=os.environ['ANTHROPIC_API_KEY']; model=os.getenv('ANTHROPIC_MODEL','claude-sonnet-5')
    r=requests.post('https://api.anthropic.com/v1/messages',headers={'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'},json={'model':model,'max_tokens':350,'messages':[{'role':'user','content':prompt}]},timeout=90)
    r.raise_for_status(); j=r.json(); text=''.join(x.get('text','') for x in j.get('content',[]) if x.get('type')=='text')
    return model,text

def gemini(prompt):
    key=os.environ['GEMINI_API_KEY']; model=os.getenv('GEMINI_MODEL','gemini-3.8-flash')
    u=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}'
    r=requests.post(u,json={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'maxOutputTokens':350}},timeout=90)
    r.raise_for_status(); j=r.json(); text=''.join(p.get('text','') for c in j.get('candidates',[]) for p in c.get('content',{}).get('parts',[]))
    return model,text

def score(case,obj):
    status=str(obj.get('relationship_status','')).upper()
    required=all(k in obj for k in ['relationship_status','directness','missing_link','reason'])
    if not required: return 0
    if case.get('expected') and status!=case['expected']: return 0
    if case.get('expected_not') and status==case['expected_not']: return 0
    return 1

def main():
    providers={'openai':openai,'anthropic':anthropic,'gemini':gemini}
    report={}; failed=[]
    for name,fn in providers.items():
        rows=[]; total=0
        for case in CASES:
            t=time.perf_counter()
            try:
                model,text=fn(case['prompt']); obj=extract_json(text); s=score(case,obj); total+=s
                rows.append({'case':case['id'],'ok':bool(s),'latency_s':round(time.perf_counter()-t,2),'model':model,'result':obj})
            except Exception as e:
                rows.append({'case':case['id'],'ok':False,'error':str(e)[:500]})
        report[name]={'score':total,'max':len(CASES),'cases':rows}
        if total < len(CASES): failed.append(name)
    (OUT/'model_eval.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:{'score':v['score'],'max':v['max']} for k,v in report.items()},ensure_ascii=False))
    if failed: raise SystemExit('Model evaluation failed: '+', '.join(failed))
if __name__=='__main__': main()
