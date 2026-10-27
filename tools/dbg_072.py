import os, json, glob
for k in ['all_proxy','ALL_PROXY','http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY']:
    os.environ.pop(k,None)
for l in open('keys.txt',encoding='utf-8'):
    if l.startswith('BAILIAN_API_KEY='):
        os.environ['BAILIAN_API_KEY']=l.strip().split('=',1)[1]
os.environ['DST_DATASET']='data/dataset_6_clean'
import sys; sys.path.insert(0,'.')
from features import Features
from pipeline import MultiIntentPipeline, load_config
from session_eval import run_session, turn_gt, judge_turn
sess={}
for fp in glob.glob('data/dataset_6_clean/sessions.T3-3.jsonl'):
    for l in open(fp,encoding='utf-8'):
        d=json.loads(l); sess[d['session_id']]=d
s=sess['S-000072']
mi_on=Features.from_config(load_config('configs/config_d6_qwen_plus_cand.json')).multi_instance_key
pipe=MultiIntentPipeline(load_config('configs/config_d6_qwen_plus_cand.json'))
pred,pt=run_session(pipe,s,retrieve_on=True)
tg=turn_gt(s,mi_on=mi_on)
tm,tt,td=judge_turn(tg,pt)
for d in td:
    if not d['match']:
        print('t',d['turn'],d['wrong'])
        print(' GT :',{k:v for k,v in d['gt'].items() if v})
        print(' PRED:',{k:v for k,v in d['pred'].items() if v})
for p in pt:
    if p['turn']==2:
        print('t2 preds:',p.get('preds'))
