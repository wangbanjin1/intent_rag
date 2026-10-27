# -*- coding: utf-8 -*-
"""扫描: 用户明说某字段值, 但 GT 最终值与之不同, 且 session 内无任何纠正(用户改口/客服澄清)。
即"用户明显说错但没人纠正"的疑似噪声。覆盖 find_noise R1-R4 未覆盖的 应用/分辨率/时延 字段。
"""
import glob, json, re, sys, io
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import schema
from case_format import flatten_params

DATASET = "data/dataset_merged"
APPS = sorted(schema.KNOWN_APPLICATIONS, key=len, reverse=True)
CORR_ACTS = {"Negate", "Correct_Previous_Input", "Modify_Request"}
CORR_WORDS = ("不是", "不对", "错了", "改成", "改为", "换成", "应该是",
              "我说的是", "搞错", "弄错", "重新", "更正", "还是不")


def norm_res(v):
    m = re.search(r"(\d{3,4})", v or "")
    return m.group(1) + "p" if m else None


def norm_rtt(v):
    m = re.search(r"(\d+)", v or "")
    return m.group(1) if m else None


hits = []
n = 0
for f in sorted(glob.glob(f"{DATASET}/sessions.T*.jsonl")):
    for line in open(f, encoding="utf-8"):
        s = json.loads(line)
        n += 1
        evs = s.get("event_sequence") or []
        text = " ".join((e.get("user") or {}).get("utterance", "") for e in evs)
        agent_text = " ".join((e.get("agent") or {}).get("utterance", "") for e in evs)

        # 有纠正痕迹的 session 跳过(说错已被纠正, 属设计)
        acts = {a for e in evs for a in ((e.get("user") or {}).get("action_types") or [])}
        if acts & CORR_ACTS:
            continue
        if any(w in text for w in CORR_WORDS) or any(w in agent_text for w in ("更正", "不匹配", "纠正")):
            continue
        if (s.get("session_meta") or {}).get("conflict"):
            continue

        stated_apps = [a for a in APPS if a in text]

        # 按分句切分, 每个 intent 只取"提到该 GT 应用名的分句"里的明说值, 避免多意图张冠李戴
        segs = re.split(r"[。；;！？!?\n]|(?:另外)|(?:还有)|(?:然后再)|(?:，再)", text)

        def segs_of(app):
            hit = [g for g in segs if app in g]
            return hit if hit else segs  # 应用名没出现则退化为全文(配合应用缺失检查)

        for it in (s.get("intents") or []):
            if it.get("status") != "resolved":
                continue
            flat = flatten_params(it.get("params") or {})
            app = flat.get("application_name")
            if not app:
                continue
            my = "。".join(segs_of(app))
            stated_res = set(m.group(1) + "p" for m in re.finditer(r"(\d{3,4})\s*[pP]", my))
            stated_rtt = set(re.findall(r"(\d+)\s*毫秒", my)) | set(re.findall(r"(\d+)\s*[mM][sS]", my))
            problems = []
            # 应用: GT 应用名全文未出现, 但用户明说了别的已知应用
            if app not in text and stated_apps:
                problems.append(f"应用: 用户明说{stated_apps} 但GT={app}")
            # 分辨率: 该应用分句内明说了 p 值但不含 GT
            gt_res = norm_res(flat.get("resolution"))
            if gt_res and stated_res and gt_res not in stated_res:
                problems.append(f"分辨率: 分句明说{sorted(stated_res)} 但GT={gt_res} | 分句: {my[:80]}")
            # 时延: 同理
            gt_rtt = norm_rtt(flat.get("rtt"))
            if gt_rtt and stated_rtt and gt_rtt not in stated_rtt:
                problems.append(f"时延: 分句明说{sorted(stated_rtt)}ms 但GT={gt_rtt}ms | 分句: {my[:80]}")
            if problems:
                hits.append((s["session_id"], s["session_meta"]["template_id"],
                             it["intent_id"], problems, text[:160]))

print(f"扫描 {n} session, 命中 {len(hits)} 处")
for sid, tid, iid, probs, txt in hits:
    print(f"\n{sid} [{tid}] {iid}")
    for p in probs:
        print(f"  {p}")
    print(f"  用户: {txt}")
