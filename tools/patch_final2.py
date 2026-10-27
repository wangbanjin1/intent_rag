import io
# 残留修正: report.md "当前最佳 = v27.0"、README 主配置说明
p='docs/report.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('## 当前水平（当前最佳 = **v27.0 @ dataset_6_clean**；下表 dataset_5 为训练/主数据集口径，逐轮真实透传，取消计分 v17.1 + 改口豁免 v20.2 + GT 知二推一 v25.1）',
'## 当前水平（当前最佳 = **v27.2.1 @ dataset_6_clean**；下表 dataset_5 为训练/主数据集口径，逐轮真实透传，取消计分 v17.1 + 改口豁免 v20.2 + GT 知二推一 v25.1）')
io.open(p,'w',encoding='utf-8').write(s)
p='README.md'
s=io.open(p,encoding='utf-8').read()
s=s.replace('主配置：`configs/config_d6_qwen_plus_best.json`（v27.0 六时间开关全开；','主配置：`configs/config_d6_qwen_plus_best.json`（v27.2.1 六时间开关+候选模式+quality_hint 全开；')
io.open(p,'w',encoding='utf-8').write(s)
print('residual fixed')
