"""_oracle_pass2.py — 同配置双跑产物对比: 采样多样性 / 翻转 / oracle pass@2 上限。

用法: python _oracle_pass2.py <runA.jsonl> <runB.jsonl>
判定 (report_reflection.md §7.5 P2 门控): oracle 上限 >3pp 才保留 BoN+verifier 路线。
"""
import json
import sys
from collections import Counter


def load(path):
    d = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            d[r["session_id"]] = r
    return d


def main(pa: str, pb: str) -> None:
    A, B = load(pa), load(pb)
    common = sorted(set(A) & set(B))
    print(f"A={pa} ({len(A)})  B={pb} ({len(B)})  overlap={len(common)}")

    def turn_map(r):
        m = {}
        for t in r["turn_detail"]:
            k = (t["turn"], t["intent_key"])
            # 同 key 双意图: 任一错记错, pred 取第一条
            prev = m.get(k)
            m[k] = ((prev[0] and t["match"]) if prev else t["match"],
                    json.dumps(t["pred"], ensure_ascii=False, sort_keys=True))
        return m

    both_right = both_wrong = flip_ab = flip_ba = pred_diff = 0
    flip_templates = Counter()
    flip_fields = Counter()
    sess_flip = 0
    for sid in common:
        ma, mb = turn_map(A[sid]), turn_map(B[sid])
        for k in set(ma) & set(mb):
            (ok_a, pr_a), (ok_b, pr_b) = ma[k], mb[k]
            if pr_a != pr_b:
                pred_diff += 1
            if ok_a and ok_b:
                both_right += 1
            elif not ok_a and not ok_b:
                both_wrong += 1
            elif ok_a:
                flip_ab += 1
                flip_templates[A[sid]["template"]] += 1
            else:
                flip_ba += 1
                flip_templates[A[sid]["template"]] += 1
        if A[sid]["session_match"] != B[sid]["session_match"]:
            sess_flip += 1

    total = both_right + both_wrong + flip_ab + flip_ba
    flip = flip_ab + flip_ba
    print(f"turn-intent cases: {total}")
    print(f"  两跑都对: {both_right} ({both_right/total*100:.2f}%)")
    print(f"  两跑都错: {both_wrong} ({both_wrong/total*100:.2f}%)")
    print(f"  翻转: {flip} ({flip/total*100:.2f}%)  [A对B错 {flip_ab} / A错B对 {flip_ba}]")
    print(f"  pred 文本不同(含等效): {pred_diff} ({pred_diff/total*100:.2f}%)")
    print(f"  session 级翻转: {sess_flip}/{len(common)}")
    print()
    print(f"单跑 A 错误率: {(both_wrong+flip_ab)/total*100:.2f}%  ->  oracle pass@2 错误率: {both_wrong/total*100:.2f}%")
    print(f"oracle 上限 = +{flip_ab/total*100:.2f}pp turn 级 (捞回 A错B对 需完美选择器)")
    print(f"门控判定: oracle 上限 {'>' if flip_ab/total*100 > 3 else '<='} 3pp -> "
          f"{'保留 BoN+verifier 路线' if flip_ab/total*100 > 3 else '采样路线整体关闭'}")
    print(f"翻转模板分布: {dict(flip_templates.most_common())}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
