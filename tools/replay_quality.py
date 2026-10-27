import os, json, glob, sys
for k in ['all_proxy','ALL_PROXY','http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY']:
    os.environ.pop(k,None)
for l in open('keys.txt',encoding='utf-8'):
    if l.startswith('BAILIAN_API_KEY='):
        os.environ['BAILIAN_API_KEY']=l.strip().split('=',1)[1]
os.environ['DST_DATASET']='data/dataset_6_clean'
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
from replay_candidate import *
from session_eval import session_gt, judge_session
all_sessions={s['session_id']:s for s in load_test_sessions('data/split_d6_full.json')}
targets=['S-000095','S-000114','S-000141','S-000211','S-000164']
pipe=MultiIntentPipeline(load_config('configs/config_d6_qwen_plus_cand.json'))
mi_on=Features.from_config(load_config('configs/config_d6_qwen_plus_best.json')).multi_instance_key
fixed=0
for sid in targets:
    s=all_sessions[sid]
    pred,pt=run_session(pipe,s,retrieve_on=True)
    gt=session_gt(s,mi_on=mi_on)
    sok,err=judge_session(gt,pred)
    print(sid,'->','PASS' if sok else 'FAIL '+err, flush=True)
    if sok: fixed+=1
print(f'{fixed}/5 修复')
