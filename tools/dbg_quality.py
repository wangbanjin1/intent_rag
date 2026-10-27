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
from session_eval import run_session, session_gt, judge_session
sess={}
for fp in glob.glob('data/dataset_6_clean/sessions.*.jsonl'):
    for l in open(fp,encoding='utf-8'):
        d=json.loads(l); sess[d['session_id']]=d
mi_on=Features.from_config(load_config('configs/config_d6_qwen_plus_cand.json')).multi_instance_key
pipe=MultiIntentPipeline(load_config('configs/config_d6_qwen_plus_cand.json'))
for sid in ['S-000042','S-000164','S-000211']:
    s=sess[sid]
    pred,pt=run_session(pipe,s,retrieve_on=True)
    gt=session_gt(s,mi_on=mi_on)
    sok,err=judge_session(gt,pred)
    print('cand rerun',sid,'match',sok,err, flush=True)
