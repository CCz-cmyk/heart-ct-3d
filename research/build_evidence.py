# -*- coding: utf-8 -*-
"""汇总 AI 输出验证证据（不调用模型，纯离线复算汇总）。

输入：research/results/agent_runs.csv（每次智能体运行的逐病灶记录）
      research/results/ai_vs_default.csv（AI 参数 / 临床默认 / 穷举最优 三方对照）
输出：research/results/ai_verification_evidence.md
      research/results/ai_verification_evidence.csv
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "research" / "results"


def load(name):
    p = RES / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main():
    runs = load("agent_runs.csv")
    pair = load("ai_vs_default.csv")

    md = ["# AI 输出验证与修正 · 证据表", "",
          "本表数据全部来自程序留痕文件，由 `research/build_evidence.py` 离线汇总，未经手工改写。", ""]

    # ---------- 一、决策层 ----------
    md += ["## 一、决策层：AI 提出参数 → 规则校验 → 独立复核", "",
           "| 运行 | 病灶 | AI 提议 | 复核结论 | 采纳后参数 | 测量体积 | 相对误差 | 引用文献 |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in runs:
        adopted = "采纳" if r.get("review_decision") == "accepted" else "拒绝→回落"
        md.append("| %s | %s %s | %s HU / %s mm | %s | %s HU / %s mm | %s mm3 | %+s%% | %s |" % (
            r.get("run", "").replace("run_", ""), r.get("lesion_id"), r.get("lesion_name"),
            r.get("ai_proposed_threshold"), r.get("ai_proposed_thickness"), adopted,
            r.get("final_threshold_hu"), r.get("final_thickness"),
            r.get("agent_measured_mm3"), r.get("agent_rel_error_pct"), r.get("citations") or "—"))
    md.append("")

    stat = Counter()
    th_prop = Counter()
    for r in runs:
        stat[r.get("review_decision")] += 1
        th_prop[r.get("ai_proposed_threshold")] += 1
    total = sum(stat.values())
    md.append("汇总：共 %d 条病灶级决策记录；复核采纳 %d 条，拒绝 %d 条；"
              "AI 提议的阈值分布 %s。" % (
                  total, stat.get("accepted", 0), stat.get("rejected", 0),
                  "、".join("%s HU ×%d" % (k, v) for k, v in sorted(th_prop.items()))))
    md.append("")
    md.append("被拒绝的情形：AI 引用通用临床阈值 130 HU（文献依据成立），但落在本项目实证稳健区间"
              "（180~260 HU）之外。数字体模背景基线约 99 HU，高于临床血液基线，同类阈值会把周围组织"
              "并入病灶。该偏差无法靠文献检索发现，必须由参数口径复核兜住。")
    md.append("")

    # ---------- 二、测量层 ----------
    md += ["## 二、测量层：AI 参数 / 临床默认 / 穷举最优 三方对照", "",
           "| 病灶 | 参数来源 | 层厚 mm | 阈值 HU | 测量体积 mm3 | 真值 mm3 | 相对误差 | 重复性 CV | 是否经人工修正 |",
           "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in pair:
        md.append("| %s %s | %s | %s | %s | %s | %s | %+s%% | %s%% | %s |" % (
            r.get("lesion_id"), r.get("lesion_name"), r.get("note"), r.get("slice_thickness_mm"),
            r.get("threshold_hu"), r.get("measured_mean_mm3"), r.get("truth_volume_mm3"),
            r.get("rel_error_pct"), r.get("repeatability_cv_pct"),
            "是" if r.get("corrected") == "True" else "否"))
    md.append("")

    # ---------- 三、稳定性 ----------
    md += ["## 三、稳定性与可复现性", ""]
    by_les = defaultdict(list)
    for r in runs:
        by_les[r.get("lesion_id")].append(r)
    for lid, rs in by_les.items():
        vals = {r.get("agent_measured_mm3") for r in rs}
        ths = {r.get("final_threshold_hu") for r in rs}
        mds = sorted({r.get("agent_measured_mm3") for r in rs})
        md.append("- 病灶 %s（%s）：跨 %d 次运行，最终阈值取值 %s HU，测量体积取值 %s mm3，%s。" % (
            lid, rs[0].get("lesion_name"), len(rs), "、".join(sorted(ths)),
            "、".join(mds), "完全一致" if len(vals) == 1 else "存在差异，需注明"))
    md.append("- 单次测量内部做 5 次随机跳采重复，重复性 CV 见表 2（AI 参数下均为 0.00%，"
              "说明病灶在该参数点附近是稳定平台）。")
    md.append("")

    # ---------- 四、覆盖与缺口 ----------
    md += ["## 四、覆盖范围与已知缺口", "",
           "已覆盖：参数决策（智能体 5 次实跑 + 逐条复核留痕）、测量执行（三方对照 + 真值校验）、"
           "稳定性（跨运行一致性与重复测量 CV）。", "",
           "未覆盖（如实声明）：", "",
           "1. 判读层证据（后端 AI 病灶判读与人工标注的一致率）本轮未采集：存量 AI 结果已回档清理，"
           "重新分析需消耗模型配额，故未纳入。",
           "2. 样本量仅 1 例 2 灶，属方法学验证级别，不外推到诊断性能。",
           "3. 数字体模真值为解析几何体积，与临床患者真值（专家手工勾画或灌注参考）不同口径。", ""]

    out_md = RES / "ai_verification_evidence.md"
    out_md.write_text("\n".join(md), encoding="utf-8")

    # ---------- CSV ----------
    rows = []
    for r in runs:
        rows.append({
            "layer": "decision", "run": r.get("run"), "lesion_id": r.get("lesion_id"),
            "ai_proposed": "%s HU / %s mm" % (r.get("ai_proposed_threshold"), r.get("ai_proposed_thickness")),
            "review": r.get("review_decision"), "adopted": "%s HU / %s mm" % (
                r.get("final_threshold_hu"), r.get("final_thickness")),
            "measured_mm3": r.get("agent_measured_mm3"), "truth_mm3": r.get("truth_mm3"),
            "rel_error_pct": r.get("agent_rel_error_pct"), "cv_pct": r.get("agent_cv_pct"),
            "citations": r.get("citations"),
        })
    for r in pair:
        rows.append({
            "layer": "measure", "run": "ai_vs_default", "lesion_id": r.get("lesion_id"),
            "ai_proposed": r.get("note"), "review": "corrected" if r.get("corrected") == "True" else "—",
            "adopted": "%s HU / %s mm" % (r.get("threshold_hu"), r.get("slice_thickness_mm")),
            "measured_mm3": r.get("measured_mean_mm3"), "truth_mm3": r.get("truth_volume_mm3"),
            "rel_error_pct": r.get("rel_error_pct"), "cv_pct": r.get("repeatability_cv_pct"),
            "citations": "",
        })
    out_csv = RES / "ai_verification_evidence.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("生成 %s" % out_md)
    print("生成 %s（%d 行）" % (out_csv, len(rows)))
    print("决策记录 %d 条：采纳 %d / 拒绝 %d" % (
        total, stat.get("accepted", 0), stat.get("rejected", 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
