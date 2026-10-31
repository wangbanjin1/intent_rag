"""format_registry 离线自检：
① 261022 原生格式投影正确（真实卡片抽 1 条）
② 新格式注册零代码改动（合成改名结构：description/latency_sla/schedule/service_info）
③ required_roles 校验能拦住缺字段数据
④ match 按键签名在多个已注册格式中选对
"""
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from format_registry import load, match, project, register, validate_view

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ① 原生格式
spec = load("semi_261022")
card = json.loads(open("data/semi_structured_dataset/261022.jsonl", encoding="utf-8").readline())
view = project(card, spec)
assert validate_view(view, spec) == [], f"原生卡片校验失败: {validate_view(view, spec)}"
assert view["goal"] == card["goal"] and view["latency_ms"] == card["sla"]["max_latency_ms"]
assert view["time_start"] == card["lifetime"]["start_timestamp"]
assert view["identity"]["application_name"] == card["context"]["application_name"]
logger.info("[OK] 261022 原生格式投影+校验")

# ② 改名新格式：注册即可用，不改代码（合成数据，非测试集内容）
tmp = tempfile.mkdtemp()
new_spec = {
    "format_id": "demo_renamed", "version": 1,
    "description": "合成改名结构：description/latency_sla/schedule/service_info",
    "key_roles": {
        "description": "goal",
        "latency_sla.latency_cap_ms": "latency_ms",
        "schedule.begin": "time_start",
        "schedule.end": "time_end",
        "schedule.minutes": "duration_min",
        "service_info": "identity",
    },
    "identity_map": {"app": "application_name", "svc": "service_name"},
    "required_roles": ["goal", "latency_ms", "time_start", "time_end"],
}
register(new_spec, registry_dir=tmp)
register(spec, registry_dir=tmp)
fake = {"description": "保障测试应用视频会议60分钟，时延不超过50ms。",
        "latency_sla": {"latency_cap_ms": 50},
        "schedule": {"begin": "2026年10月23日20时00分", "end": "2026年10月23日21时00分", "minutes": 60},
        "service_info": {"app": "测试应用", "svc": "视频会议"}}
view2 = project(fake, load("demo_renamed", registry_dir=tmp))
assert validate_view(view2, new_spec) == []
assert view2["latency_ms"] == 50 and view2["identity"]["application_name"] == "测试应用"
assert view2["time_start"] == "2026年10月23日20时00分"
logger.info("[OK] 新格式注册零代码改动（合成改名结构投影正确）")

# ③ 缺字段拦截
bad = dict(fake)
bad["latency_sla"] = {}
errs = validate_view(project(bad, new_spec), new_spec)
assert any("latency_ms" in e for e in errs), errs
logger.info("[OK] required_roles 缺字段拦截")

# ④ match 签名匹配（两格式共存时各选各的）
assert match(card, registry_dir=tmp)["format_id"] == "semi_261022"
assert match(fake, registry_dir=tmp)["format_id"] == "demo_renamed"
logger.info("[OK] match 按键签名正确分流")

logger.info("format_registry 全部自检通过 [ALL OK]")
