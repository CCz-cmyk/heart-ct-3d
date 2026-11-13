# -*- coding: utf-8 -*-
"""科研智能体（含 Harness）：把「知识库检索 → ROI 特征 → 参数试算 → 合理性校验 → 结论」
串成一次可观测、可复现的多步工具调用过程。

Harness 负责（不是模型负责）：
  1. 工具白名单 + 参数 schema 校验（越界参数在执行前被拒绝）
  2. 单步重试上限、全局步数上限、单次调用超时
  3. 全链路 trace（每步 thought / tool / args / result / 耗时 / token）
  4. 终止兜底：模型没给结论时，回落到临床默认参数并标记原因
  5. 真值隔离：测量工具只回“测出来多少”，不回真值与误差，
     真值对照在过程结束后由 harness 侧统一计算，避免 AI 看着答案调参。

用法（项目根目录）：
    backend\\venv\\Scripts\\python.exe research\\agent.py
    backend\\venv\\Scripts\\python.exe research\\agent.py --case 1 --max-steps 10

凭证：research/config.local.json（不写入代码、不进报告）
输出：research/results/agent_trace.jsonl
      research/results/agent_report.md
      research/results/agent_summary.csv
"""

import argparse
import csv
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import ai_param as ap                      # 复用模型调用、特征提取、测量与校验
from kb_search import KB, format_for_prompt

CFG_PATH = ROOT / "research" / "config.local.json"
OUT_DIR = ROOT / "research" / "results"
VOL_DIR = ROOT / "data" / "volumes"

MAX_STEPS_DEFAULT = 10
STEP_RETRY = 2
TOOL_TIMEOUT = 25

# 独立复核规则：来自本项目单因素网格实验的实证结论，而不是逐例真值。
# 阈值 150 HU 档实测过估 +20%~+56%，阈值 >=300 HU 档混合斑块漏检，故稳健区间取 180~260 HU；
# 层厚网格实测误差随层厚单调放大，故只接受 1~2 mm。
ROBUST_TH = (180, 260)
ROBUST_T = (1, 2)
ROBUST_FALLBACK = {"threshold_hu": 200, "slice_thickness_mm": 1}
ROBUST_SOURCE = "research/results/experiment_summary.csv 单因素网格实验（阈值 × 层厚 × 偏移）"

TOOLS = ("kb_search", "roi_stats", "measure", "verify", "finish")

SYSTEM_PROMPT = """你是心血管影像定量方向的科研智能体，任务是为一枚已知病灶确定体积量化的测量参数。

可用工具（只能调用这些，参数越界会被 Harness 拒绝）：
- kb_search {"query": 字符串, "k": 1-5}          检索本课题知识库，拿文献依据（返回带编号 KB01..KB10 的条目）
- roi_stats {"lesion_id": 整数}                  读体数据，返回该病灶 ROI 与背景的 HU 统计
- measure {"thickness": 1-5, "threshold": 100-600}  按给定层厚与阈值执行体积测量（含 5 次重复的均值/SD/CV）
- verify {"threshold_hu": 100-600, "slice_thickness_mm": 1-5}  执行合理性校验，返回是否越界与修正建议
- finish {"threshold_hu": 整数, "slice_thickness_mm": 整数, "reason": 字符串, "citations": ["KB07"], "uncertainty": 字符串}

工作方式：每一步只输出一个 JSON 对象，不要输出别的文字：
{"thought": "本步为什么这么做（30 字内）", "tool": "工具名", "args": {工具参数}}

硬性要求：
1. 选参数前必须先 kb_search 拿文献依据，最终 citations 里的编号必须来自真实检索结果；
2. 选参数要结合 roi_stats 的背景均值、背景标准差与病灶峰值，阈值必须高于背景、低于峰值；
3. 给出最终参数前必须调 verify 校验一次；若 verify 判定不通过，按返回的可用区间修正后重新 verify；
4. finish 前必须调 measure 至少试算一次，确认该参数下能测出体积；measure 只回测量值，不回真值；
5. 试算不超过 2 次，不要靠反复调参凑数；
6. 拿不准就在 uncertainty 里写明，不允许编造文献结论或具体数字。"""


class Harness:
    def __init__(self, cfg, kb, vol, lesions, features, trace_path, max_steps=MAX_STEPS_DEFAULT,
                 run_tag="run"):
        self.cfg = cfg
        self.kb = kb
        self.vol = vol
        self.lesions = lesions
        self.features = features
        self.trace_path = trace_path
        self.max_steps = max_steps
        self.run_tag = run_tag
        self.tool_calls = 0
        self.rejected = 0
        self.retried = 0

    # ---------- 确定性测量种子：同一参数任何时候都得到同一结果 ----------
    def _rng_for(self, lid, t, th):
        seed = (int(lid) * 100003 + int(t) * 1009 + int(th)) % (2 ** 31)
        return np.random.default_rng(seed)

    def _default_ref(self, lid):
        """默认参数对照统一取主实验产出的 ai_vs_default.csv，避免出现第二套口径。"""
        p = ROOT / "research" / "results" / "ai_vs_default.csv"
        if not p.exists():
            return None
        with open(p, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if row.get("lesion_id") == str(lid) and row.get("param_source") == "default":
                    return {"measured_mm3": float(row["measured_mean_mm3"]),
                            "rel_error_pct": float(row["rel_error_pct"]),
                            "cv_pct": float(row["repeatability_cv_pct"])}
        return None

    # ---------- 日志 ----------
    def log(self, rec):
        rec["timestamp"] = datetime.now().isoformat(timespec="seconds")
        rec["run"] = self.run_tag
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------- 参数校验（Harness 的职责，不是模型的自律） ----------
    def validate(self, tool, args):
        if tool not in TOOLS:
            return False, "工具 %r 不在白名单 %s 内" % (tool, list(TOOLS))
        if not isinstance(args, dict):
            return False, "args 必须是 JSON 对象"

        def num(key, lo, hi, required=True):
            if key not in args:
                if required:
                    return None, "缺少参数 %s" % key
                return None, None
            try:
                v = int(args[key])
            except Exception:
                return None, "参数 %s 必须是整数，收到 %r" % (key, args[key])
            if v < lo or v > hi:
                return None, "参数 %s=%d 越界，允许范围 %d..%d" % (key, v, lo, hi)
            return v, None

        if tool == "kb_search":
            q = args.get("query")
            if not isinstance(q, str) or not q.strip():
                return False, "query 必须是非空字符串"
            k, err = num("k", 1, 5, required=False)
            return (True, {"query": q.strip(), "k": k or 3}) if not err else (False, err)

        if tool == "roi_stats":
            lid, err = num("lesion_id", 1, 99)
            if err:
                return False, err
            if lid not in self.features:
                return False, "lesion_id=%d 不在本病例病灶列表 %s 内" % (lid, sorted(self.features))
            return True, {"lesion_id": lid}

        if tool == "measure":
            t, err = num("thickness", 1, 5)
            if err:
                return False, err
            th, err = num("threshold", 100, 600)
            if err:
                return False, err
            return True, {"thickness": t, "threshold": th}

        if tool == "verify":
            th, err = num("threshold_hu", 100, 600)
            if err:
                return False, err
            t, err = num("slice_thickness_mm", 1, 5)
            if err:
                return False, err
            return True, {"threshold_hu": th, "slice_thickness_mm": t}

        if tool == "finish":
            th, err = num("threshold_hu", 100, 600)
            if err:
                return False, err
            t, err = num("slice_thickness_mm", 1, 5)
            if err:
                return False, err
            cites = args.get("citations") or []
            if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
                return False, "citations 必须是文献编号字符串数组"
            known = {p["id"] for p in self.kb.papers}
            bad = [c for c in cites if c not in known]
            if bad:
                return False, "引用了知识库里不存在的编号 %s" % bad
            return True, {"threshold_hu": th, "slice_thickness_mm": t,
                          "reason": str(args.get("reason", ""))[:300],
                          "citations": cites, "uncertainty": str(args.get("uncertainty", ""))[:300]}

        return False, "未实现该工具"

    # ---------- 工具执行 ----------
    def execute(self, tool, args, les, f):
        if tool == "kb_search":
            hits = self.kb.search(args["query"], args["k"])
            return {"hits": [{"id": h["id"], "title": h["title"], "venue": h["venue"],
                              "findings": h["findings"], "relevance": h["relevance"]} for h in hits],
                    "context_block": format_for_prompt(hits)}

        if tool == "roi_stats":
            return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items()}

        if tool == "measure":
            m = ap.measure_repeated(self.vol, les, args["thickness"], args["threshold"],
                                    self._rng_for(les["id"], args["thickness"], args["threshold"]))
            # 真值隔离：只回测量侧的量，不回 truth / rel_error
            return {"thickness": args["thickness"], "threshold_hu": args["threshold"],
                    "measured_mean_mm3": m["measured_mean_mm3"],
                    "measured_sd_mm3": m["measured_sd_mm3"],
                    "repeatability_cv_pct": m["repeatability_cv_pct"]}

        if tool == "verify":
            # 只校验真正影响分割结果的量：阈值必须高于背景、低于病灶峰值。
            # 窗宽窗位不改变 HU 阈值分割的结果，因此不参与 blocking 判定。
            th = args["threshold_hu"]
            lo = f["bg_mean"] + 10.0
            hi = f["roi_max"] * 0.9
            notes = []
            blocking = False
            if th < lo:
                blocking = True
                notes.append("阈值 %d HU 低于背景均值+10（%.1f HU），背景组织会被误判为病灶" % (th, lo))
            if th > hi:
                blocking = True
                notes.append("阈值 %d HU 高于病灶峰值 90%%（%.1f HU），病灶会被整块漏检" % (th, hi))
            return {"passed": not blocking, "blocking": blocking,
                    "allowed_range_hu": [int(round(lo)), int(round(hi))],
                    "notes": notes,
                    "hint": "可用阈值区间约 %d ~ %d HU" % (int(round(lo)), int(round(hi)))}

        return {}

    # ---------- 单病灶主循环 ----------
    def run_lesion(self, les):
        lid = les["id"]
        f = self.features[lid]
        print("\n=== 病灶 %d：%s（r=%d，峰值 %.0f HU，背景 %.1f±%.1f）===" % (
            lid, les["name"], les["radius"], f["roi_max"], f["bg_mean"], f["bg_sd"]))

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                "当前任务：为病灶 lesion_id=%d（%s，半径 %d 体素）确定体积量化参数。"
                "已知扫描名义层厚 1 mm、体素 1×1×1 mm，测量按 Cavalieri 原理跳采积分。"
                "请开始第 1 步。" % (lid, les["name"], int(les["radius"])))},
        ]

        final = None
        history = []
        steps = 0
        while steps < self.max_steps:
            steps += 1
            content, usage, elapsed = ap.call_ark(self.cfg, messages, max_tokens=800)
            act = ap.parse_json(content)
            self.log({"lesion_id": lid, "step": steps, "phase": "llm", "raw": content,
                      "parsed": act, "usage": usage, "elapsed": round(elapsed, 2)})
            print("  [step %d] 模型输出：%s" % (steps, (content or "").replace("\n", " ")[:120]))

            if not isinstance(act, dict) or "tool" not in act:
                ok, msg = False, "上一步没有按格式输出 JSON（需含 tool 与 args），请重新输出"
                self.rejected += 1
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "user", "content": json.dumps(
                    {"ok": ok, "error": msg}, ensure_ascii=False)})
                continue

            tool = act.get("tool")
            args = act.get("args") or {}
            ok, payload = self.validate(tool, args)
            if not ok:
                self.rejected += 1
                self.log({"lesion_id": lid, "step": steps, "phase": "reject", "tool": tool,
                          "args": args, "error": payload})
                print("  [harness] 拒绝执行：%s" % payload)
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": json.dumps(
                    {"ok": False, "error": payload, "allowed": list(TOOLS)}, ensure_ascii=False)})
                continue

            if tool == "finish":
                self.log({"lesion_id": lid, "step": steps, "phase": "finish",
                          "tool": tool, "args": payload})
                final = payload
                history.append({"step": steps, "tool": "finish", "args": payload})
                print("  [finish] 阈值 %d HU，层厚 %d，引用 %s" % (
                    payload["threshold_hu"], payload["slice_thickness_mm"], payload["citations"]))
                break

            t0 = time.time()
            try:
                result = self.execute(tool, payload, les, f)
                err = None
            except Exception as e:
                result, err = None, "%s: %s" % (type(e).__name__, str(e)[:200])
                self.retried += 1
                traceback.print_exc()
            dt = time.time() - t0
            self.tool_calls += 1
            self.log({"lesion_id": lid, "step": steps, "phase": "tool", "tool": tool,
                      "args": payload, "ok": err is None, "error": err,
                      "result": result, "elapsed": round(dt, 2)})
            history.append({"step": steps, "tool": tool, "args": payload,
                            "ok": err is None, "result": result})
            print("  [harness] %s 执行%s（%.2fs）" % (tool, "失败" if err else "成功", dt))

            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": json.dumps(
                {"ok": err is None, "error": err, "result": result,
                 "note": "measure 只返回测量值，不含真值；请勿猜测真值。"},
                ensure_ascii=False)})

        if final is None:
            # 兜底：模型未给出结论，按默认参数执行并明确标记
            final = {"threshold_hu": ap.DEFAULT_THRESHOLD, "slice_thickness_mm": ap.DEFAULT_THICKNESS,
                     "reason": "智能体未在步数上限内给出结论，Harness 回落临床默认参数",
                     "citations": [], "uncertainty": "结论来自兜底规则，非模型输出"}
            self.log({"lesion_id": lid, "step": steps, "phase": "fallback", "args": final})
            print("  [harness] 触发兜底：使用临床默认参数")

        # ---- 独立复核：用实验得出的稳健区间约束 AI 结论（不是逐例真值，可迁移）----
        th_ai = final.get("threshold_hu")
        t_ai = final.get("slice_thickness_mm")
        review = {"decision": "accepted", "notes": [],
                  "ai_proposal": {"threshold_hu": th_ai, "slice_thickness_mm": t_ai},
                  "rule_basis": ROBUST_SOURCE}
        if th_ai is None or not (ROBUST_TH[0] <= int(th_ai) <= ROBUST_TH[1]):
            review["decision"] = "rejected"
            review["notes"].append(
                "阈值 %s HU 落在实证稳健区间 %d~%d 之外：网格实验中低阈值档（150 HU）已出现过估超过 20%% 的情形，"
                "研讨体模背景基线偏高，沿用临床 130 HU 会把周围组织并入病灶" % (th_ai, ROBUST_TH[0], ROBUST_TH[1]))
        if t_ai is None or not (ROBUST_T[0] <= int(t_ai) <= ROBUST_T[1]):
            review["decision"] = "rejected"
            review["notes"].append(
                "层厚 %s mm 超出 %d~%d mm 的可接受范围：层厚网格实测误差随层厚单调放大" % (
                    t_ai, ROBUST_T[0], ROBUST_T[1]))
        if review["decision"] == "rejected":
            review["fallback"] = dict(ROBUST_FALLBACK)
            self.log({"lesion_id": lid, "phase": "review", **review})
            print("  [review] AI 提议（%s HU / %s mm）被复核拒绝：%s" % (
                th_ai, t_ai, "；".join(review["notes"])))
            final = {"threshold_hu": ROBUST_FALLBACK["threshold_hu"],
                     "slice_thickness_mm": ROBUST_FALLBACK["slice_thickness_mm"],
                     "reason": "独立复核拒绝 AI 提议（%s HU / %s mm），回落实证稳健参数 200 HU / 1 mm" % (th_ai, t_ai),
                     "citations": final.get("citations") or [],
                     "uncertainty": "最终参数并非 AI 原始输出，已被复核环节替换"}
        else:
            self.log({"lesion_id": lid, "phase": "review", **review})
            print("  [review] AI 提议（%s HU / %s mm）通过复核" % (th_ai, t_ai))

        # ---- 真值对照（过程结束后统一计算，模型全程看不到）----
        m = ap.measure_repeated(self.vol, les, final["slice_thickness_mm"], final["threshold_hu"],
                                self._rng_for(lid, final["slice_thickness_mm"], final["threshold_hu"]))
        d = self._default_ref(lid)
        if d is None:
            dm = ap.measure_repeated(self.vol, les, ap.DEFAULT_THICKNESS, ap.DEFAULT_THRESHOLD,
                                     self._rng_for(lid, ap.DEFAULT_THICKNESS, ap.DEFAULT_THRESHOLD))
            d = {"measured_mm3": dm["measured_mean_mm3"],
                 "rel_error_pct": dm["rel_error_pct"], "cv_pct": dm["repeatability_cv_pct"]}
        row = {
            "case_id": ap.CASE_ID, "lesion_id": lid, "lesion_name": les["name"],
            "steps": steps, "tool_calls": sum(1 for h in history if h["tool"] != "finish"),
            "final_threshold_hu": final["threshold_hu"],
            "final_thickness": final["slice_thickness_mm"],
            "review_decision": review["decision"],
            "ai_proposed_threshold": th_ai, "ai_proposed_thickness": t_ai,
            "agent_measured_mm3": m["measured_mean_mm3"], "truth_mm3": m["truth_volume_mm3"],
            "agent_rel_error_pct": m["rel_error_pct"], "agent_cv_pct": m["repeatability_cv_pct"],
            "default_measured_mm3": d["measured_mm3"],
            "default_rel_error_pct": d["rel_error_pct"], "default_cv_pct": d["cv_pct"],
            "citations": ";".join(final.get("citations") or []),
            "reason": final.get("reason", ""), "uncertainty": final.get("uncertainty", ""),
        }
        self.log({"lesion_id": lid, "phase": "result", "result": row})
        print("  真值 %.1f mm3 | 智能体 %.1f（%+.2f%%，CV %.2f%%）| 默认 %.1f（%+.2f%%）" % (
            row["truth_mm3"], row["agent_measured_mm3"], row["agent_rel_error_pct"],
            row["agent_cv_pct"], row["default_measured_mm3"], row["default_rel_error_pct"]))
        return row, history


def write_report(rows, histories, meta, path):
    lines = ["# 科研智能体运行报告", "",
             "- 生成时间：%s" % meta["generated_at"],
             "- 模型：%s" % meta["model"],
             "- 知识库：%s（%d 条文献）" % (meta["kb_name"], meta["kb_size"]),
             "- 工具调用：%d 次；被 Harness 拒绝的非法动作：%d 次；工具异常重试：%d 次" % (
                 meta["tool_calls"], meta["rejected"], meta["retried"]),
             "- 真值隔离：运行期间模型只能看到测量值，真值与误差在过程结束后统一计算", "",
             "## 一、AI 输出校验与修正汇总", "",
             "| 病灶 | 步 | AI 提议的参数 | Harness 判定 | 处理结果 |",
             "| --- | --- | --- | --- | --- |"]

    rejected_n = 0
    for r, h in zip(rows, histories):
        for s in h:
            if s["tool"] != "verify":
                continue
            res = s.get("result") or {}
            rng = res.get("allowed_range_hu") or [0, 0]
            prop = "%s HU / %s mm" % (s["args"].get("threshold_hu"), s["args"].get("slice_thickness_mm"))
            if res.get("passed"):
                verdict = "通过（可用区间 %d~%d HU）" % (rng[0], rng[1])
                action = "采纳"
            else:
                rejected_n += 1
                verdict = "不通过"
                action = "拒绝并回灌：%s" % "；".join(res.get("notes") or [])
            lines.append("| %d · %s | %d | %s | %s | %s |" % (
                r["lesion_id"], r["lesion_name"], s["step"], prop, verdict, action))
    lines.append("")
    lines.append("运行期间共 %d 次参数校验，其中 %d 次被 Harness 判定不通过并强制修正。" % (
        sum(1 for h in histories for s in h if s["tool"] == "verify"), rejected_n))
    lines.append("")

    lines.append("## 二、逐病灶运行结果")
    lines.append("")
    for r, h in zip(rows, histories):
        lines.append("### 病灶 %d · %s" % (r["lesion_id"], r["lesion_name"]))
        lines.append("")
        lines.append("| 项 | 值 |")
        lines.append("| --- | --- |")
        lines.append("| AI 原始提议 | %s HU / %s mm |" % (r["ai_proposed_threshold"], r["ai_proposed_thickness"]))
        lines.append("| 复核结论 | %s |" % ("采纳 AI 提议" if r["review_decision"] == "accepted"
                                         else "拒绝 AI 提议，回落实证稳健参数"))
        lines.append("| 智能体最终参数 | 阈值 %d HU，层厚 %d |" % (r["final_threshold_hu"], r["final_thickness"]))
        lines.append("| 调用步数 / 工具调用 | %d / %d |" % (r["steps"], r["tool_calls"]))
        lines.append("| 测量体积 | %.1f mm3 |" % r["agent_measured_mm3"])
        lines.append("| 真值体积 | %.1f mm3 |" % r["truth_mm3"])
        lines.append("| 相对误差 | %+.2f%% |" % r["agent_rel_error_pct"])
        lines.append("| 重复性 CV | %.2f%% |" % r["agent_cv_pct"])
        lines.append("| 临床默认参数对照 | %.1f mm3（%+.2f%%） |" % (
            r["default_measured_mm3"], r["default_rel_error_pct"]))
        lines.append("| 引用文献 | %s |" % (r["citations"] or "无"))
        lines.append("| 决策理由 | %s |" % (r["reason"] or "—"))
        lines.append("| 不确定性声明 | %s |" % (r["uncertainty"] or "—"))
        lines.append("")
        lines.append("**执行轨迹**（每步工具与关键返回）：")
        lines.append("")
        for h in h:
            if h["tool"] == "finish":
                lines.append("- step %d · finish → 阈值 %s HU / 层厚 %s" % (
                    h["step"], h["args"]["threshold_hu"], h["args"]["slice_thickness_mm"]))
            else:
                brief = ""
                res = h.get("result") or {}
                if h["tool"] == "kb_search":
                    brief = "命中 " + "、".join(x["id"] for x in res.get("hits", []))
                elif h["tool"] == "roi_stats":
                    brief = "背景 %.1f±%.1f，峰值 %.0f" % (res.get("bg_mean", 0), res.get("bg_sd", 0), res.get("roi_max", 0))
                elif h["tool"] == "measure":
                    brief = "t=%s th=%s → %.1f mm3（CV %.2f%%）" % (
                        res.get("thickness"), res.get("threshold_hu"),
                        res.get("measured_mean_mm3", 0), res.get("repeatability_cv_pct", 0))
                elif h["tool"] == "verify":
                    rng = res.get("allowed_range_hu") or [0, 0]
                    if res.get("passed"):
                        brief = "通过（可用区间 %d~%d HU）" % (rng[0], rng[1])
                    else:
                        brief = "拦截：%s" % "；".join(res.get("notes") or [])
                lines.append("- step %d · %s %s → %s" % (h["step"], h["tool"], h["args"], brief))
        lines.append("")

    lines.append("## 三、独立复核（AI 结论的采纳与否）")
    lines.append("")
    lines.append("复核规则来自本项目单因素网格实验的实证结论，不依赖逐例真值，因此可迁移到新病例："
                 "阈值稳健区间 **%d~%d HU**，层厚 **%d~%d mm**；越界则回落 200 HU / 1 mm（来源：%s）。" % (
                     ROBUST_TH[0], ROBUST_TH[1], ROBUST_T[0], ROBUST_T[1], ROBUST_SOURCE))
    lines.append("")
    lines.append("| 病灶 | AI 原始提议 | 复核结论 | 采纳/回落后的参数 |")
    lines.append("| --- | --- | --- | --- |")
    for r in rows:
        lines.append("| %d · %s | %s HU / %s mm | %s | %d HU / %d mm |" % (
            r["lesion_id"], r["lesion_name"], r["ai_proposed_threshold"], r["ai_proposed_thickness"],
            "通过" if r["review_decision"] == "accepted" else "拒绝（越出稳健区间）",
            r["final_threshold_hu"], r["final_thickness"]))
    lines.append("")
    if rejected_n:
        lines.append("被拒绝的记录逐条列在上表；拒绝理由均来自实证区间，不是逐例真值。")
    else:
        lines.append("本次运行中 AI 提议全部落在实证稳健区间内，未触发拒绝。")
    lines.append("")
    lines.append("## 四、Harness 留痕说明")
    lines.append("")
    lines.append("- 每次模型输出、每次工具调用、每次拒绝与修正都逐行写入 `agent_trace.jsonl`；")
    lines.append("- 非法参数（越界阈值/层厚、不存在的文献编号、未知工具）在执行前被拦截，并把错误回灌给模型重试；")
    lines.append("- 模型步数超限时由 Harness 回落临床默认参数，避免“无限试算”式伪收敛。")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    apx = argparse.ArgumentParser()
    apx.add_argument("--case", type=int, default=ap.CASE_ID)
    apx.add_argument("--max-steps", type=int, default=MAX_STEPS_DEFAULT)
    arg = apx.parse_args()

    ap.CASE_ID = arg.case
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not CFG_PATH.exists():
        print("ERR: 缺少 %s" % CFG_PATH)
        return 2
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    if not cfg.get("api_key") or not cfg.get("model"):
        print("ERR: config.local.json 的 api_key / model 未填写完整")
        return 2

    vol_path = VOL_DIR / ("case_%02d.npy" % arg.case)
    if not vol_path.exists():
        print("ERR: 体数据不存在 %s" % vol_path)
        return 2
    vol = np.load(str(vol_path), mmap_mode="r")
    lesions, src = ap.load_lesions(arg.case)
    print("体数据 %s shape=%s" % (vol_path.name, vol.shape))
    print("病灶来源 %s，共 %d 个" % (src, len(lesions)))

    kb = KB()
    features = {les["id"]: ap.collect_features(vol, les) for les in lesions}

    trace_path = OUT_DIR / "agent_trace.jsonl"
    run_tag = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    print("运行编号 %s（trace 累积追加，不覆盖历史）" % run_tag)

    h = Harness(cfg, kb, vol, lesions, features, trace_path, max_steps=arg.max_steps,
                run_tag=run_tag)
    rows, histories = [], []
    for les in lesions:
        try:
            row, hist = h.run_lesion(les)
        except Exception as e:
            msg = str(e)[:300]
            h.log({"lesion_id": les.get("id"), "phase": "abort", "error": msg})
            print("\nERR: 病灶 %s 运行中止：%s" % (les.get("id"), msg))
            if "429" in msg or "SetLimitExceeded" in msg or "limit" in msg.lower():
                print("提示：模型账号触发用量上限，其余病灶未执行；已完成部分的轨迹与结论仍保留。")
            break
        rows.append(row)
        histories.append(hist)

    if not rows:
        print("本次没有任何病灶完成，未生成汇总表与报告。")
        return 3

    csv_path = OUT_DIR / "agent_summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    report_path = OUT_DIR / "agent_report.md"
    write_report(rows, histories, {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": cfg["model"],
        "kb_name": kb.meta.get("name", ""),
        "kb_size": len(kb.papers),
        "tool_calls": h.tool_calls,
        "rejected": h.rejected,
        "retried": h.retried,
    }, report_path)

    runs_csv = OUT_DIR / "agent_runs.csv"
    is_new = not runs_csv.exists()
    with open(runs_csv, "a", newline="", encoding=("utf-8-sig" if is_new else "utf-8")) as fh:
        w = csv.DictWriter(fh, fieldnames=["run"] + list(rows[0].keys()))
        if is_new:
            w.writeheader()
        for r in rows:
            row = {"run": run_tag}
            row.update(r)
            w.writerow(row)

    print("\n汇总表 %s（%d 行）" % (csv_path, len(rows)))
    print("多次运行台账 %s" % runs_csv)
    print("运行报告 %s" % report_path)
    print("全链路留痕 %s" % trace_path)
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
