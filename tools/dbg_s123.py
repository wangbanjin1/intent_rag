import sys, json, glob
sys.path.insert(0,'.')
sys.path.insert(0,'tools')
from replay_record_cases import *
all_sessions={s['session_id']:s for s in load_test_sessions('data/split_d6_full.json')}
mi_on=Features.from_config(load_config('configs/config_d6_qwen_plus_best.json')).multi_instance_key
pipe=MultiIntentPipeline(load_config('configs/config_d6_qwen_plus_rec.json'))
s=all_sessions['S-000123']
pred,pt=run_session(pipe,s,retrieve_on=True)
tg=turn_gt(s,mi_on=mi_on)
tm,tt,td=judge_turn(tg,pt)
for d in td:
    if not d['match']:
        print('t',d['turn'],'GT rtt=',d['gt'].get('rtt'),'PRED rtt=',d['pred'].get('rtt'))
        print('raw:',d['llm_raw'][:300].replace(chr(10),' | '))
