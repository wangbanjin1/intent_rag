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
from session_eval import run_session
sess={}
for fp in glob.glob('data/dataset_6_clean/sessions.*.jsonl'):
    for l in open(fp,encoding='utf-8'):
        d=json.loads(l); sess[d['session_id']]=d
s=sess['S-000164']
mi_on=Features.from_config(load_config('configs/config_d6_qwen_plus_best.json')).multi_instance_key
# cand 配置重跑看 raw
pipe=MultiIntentPipeline(load_config('configs/config_d6_qwen_plus_cand.json'))
pred,pt=run_session(pipe,s,retrieve_on=True)
print(s['event_sequence'][0]['user']['utterance'][:80])
print('t1 raw:',pt[0].get('llm_raw','')[:350].replace(chr(10),' | '))
# v27.0 基线同输入
pipe2=MultiIntentPipeline(load_config('configs/config_d6_qwen_plus_best.json'))
pred2,pt2=run_session(pipe2,s,retrieve_on=True)
print('=== base t1 raw:',pt2[0].get('llm_raw','')[:350].replace(chr(10),' | '))
