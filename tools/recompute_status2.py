import os, json, re, sys
for k in ['all_proxy','ALL_PROXY','http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY']:
    os.environ.pop(k,None)
sys.path.insert(0,'.')
from intent_status import (build_gt_status_trajectory, derive_status, is_affirm,
                           has_pending_choice, in_matrix, judge_status)
from session_eval import load_test_sessions
all_sessions={s['session_id']:s for s in load_test_sessions('data/split_d6_full.json')}
total_old=0; total_new=0; total=0; n_old_worse=0; n_new_worse=0
for n in [1,2,3]:
    rows={json.loads(l)['session_id']:json.loads(l) for l in open(f'data/evals/eval_dual_v272_cand_run{n}.jsonl',encoding='utf-8')}
    for sid,r in rows.items():
        s=all_sessions.get(sid)
        if not s: continue
        gt_new=build_gt_status_trajectory(s, mi_on=True)
        prev={}; traj={}
        for to in r['turn_outputs']:
            t=to['turn']; utt=''
            for ev in s['event_sequence']:
                if ev['turn']==t: utt=ev['user']['utterance']
            ck=set(m.group(1).strip() for m in re.finditer(r"#\s*cancel\s*:\s*(.+)", to.get('llm_raw','')))
            st_out={}
            for k,flat in to.get('preds',{}).items():
                if not in_matrix(flat): continue
                st=derive_status(prev.get(k), flat, 'valid',
                                 cancelled=(k in ck), affirmed=is_affirm(utt),
                                 touched=True, pending_choice=has_pending_choice(utt))
                st_out[k]=st; prev[k]=st
            traj[t]=st_out
        nm,nt,nd=judge_status(gt_new,traj)
        om,ot=r.get('status_match',0), r.get('status_total',0)
        total_old+=om; total_new+=nm; total+=ot
        if nm<om: n_old_worse+=1
        if nm>om: n_new_worse+=1
print(f'三跑状态机口径: 旧GT {total_old}/{total}={total_old/total*100:.2f}%  新GT(修后) 重放 {total_new}/{total}={total_new/total*100:.2f}%')
print(f'逐session: 新口径变差 {n_old_worse} 个, 变好 {n_new_worse} 个')
