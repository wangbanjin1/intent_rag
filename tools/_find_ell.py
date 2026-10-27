# -*- coding: utf-8 -*-
"""在 3516 行已落盘的 jsonl 里没有; 崩在 results 序列化 — 崩的 session 在内存里.
日志里最后处理的 session 是 4455/4455 全部跑完. 崩在写文件 — 某个 session 的 pred_intents
含 '/看直播' 无头意图, 其 start_timestamp=Ellipsis (模型字面写 find_timestamp(start=...)).
offline 找: 重跑判分不改, 直接扫 jsonl? jsonl 只有 3516 行 (崩时 results 循环写到第 3516 个).
实际崩在哪个 session? results 顺序=加载顺序, 第 3517 个 session 即崩点.
从 jsonl 最后一行拿下一个 id, 然后 DST_DATASET 重放该 session 找 raw."""
import json
lines = open('data/evals/eval_dual_v2731_d930_q38_run1.jsonl', encoding='utf-8').read().strip().split('\n')
print('written:', len(lines))
last = json.loads(lines[-1])
print('last written:', last['session_id'])
