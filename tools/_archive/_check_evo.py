import json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import session_eval  # 先让它包 stdout
import error_report as er

sessions = er.load_sessions('data/dataset_5')
rows = {r['session_id']: r for r in er.load_eval('data/evals/eval_dual_v24_1_recrule_gtderive.jsonl')}
for sid in ('S-000094', 'S-000318', 'S-000175'):
    s = sessions[sid]; r = rows[sid]
    h = er.state_evolution_html(s, r, 1, 'test')
    parts = h.split("evo-pane' data-turn='")[1:]
    print('==', sid)
    for part in parts:
        t = part[0]
        body = part[2:]
        intents = re.findall(r"evo-intent'><b>(.*?)</b>", body)
        print('  轮' + t + ':', intents)
