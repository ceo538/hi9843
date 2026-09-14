import json
from pathlib import Path
needed=['model_eval.json','live_data_gate.json','windows_e2e.txt']
r=Path('reports')
missing=[x for x in needed if not (r/x).exists()]
if missing: raise SystemExit('Missing reports: '+', '.join(missing))
models=json.loads((r/'model_eval.json').read_text(encoding='utf-8'))
live=json.loads((r/'live_data_gate.json').read_text(encoding='utf-8'))
if not all(v.get('score')==v.get('max') for v in models.values()): raise SystemExit('Model gate not fully passed')
if not all(v.get('ok') for v in live.values()): raise SystemExit('Live data gate not fully passed')
if 'PASS' not in (r/'windows_e2e.txt').read_text(encoding='utf-8'): raise SystemExit('Windows E2E not passed')
summary={'status':'PASS','model_providers':list(models),'live_sources':list(live),'windows_e2e':'PASS'}
(r/'RELEASE_GATE_PASS.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print('RELEASE_GATE_PASS')
