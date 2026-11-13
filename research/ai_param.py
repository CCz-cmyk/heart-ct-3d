# -*- coding: utf-8 -*-
"""
第 4 环节：AI 进入计算执行 —— 大模型读切面 HU 统计特征，给出分割阈值与窗设置，
系统按 AI 参数执行体积测量，并与默认参数、穷举最优参数对照。

凭证：research\config.local.json（不写入代码、不进报告）
输出：research\results\ai_vs_default.csv
      research\results\ai_calls.jsonl（提示词 / 原始响应 / 解析结果 / 人工修正记录）
      research\results\features.json
"""

import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VOL_PATH = ROOT / "data" / "volumes" / "case_01.npy"
CFG_PATH = ROOT / "research" / "config.local.json"
OUT_DIR = ROOT / "research" / "results"

CASE_ID = 1
REPEATS = 5
SEED = 20261108

DEFAULT_THICKNESS = 3
DEFAULT_THRESHOLD = 200

FALLBACK_LESIONS = [
    {"id": 1, "name": "前降支近段钙化斑块", "cx": 132, "cy": 152, "cz": 92, "radius": 12, "ct_value": 421.0},
    {"id": 2, "name": "右冠近段混合斑块", "cx": 92, "cy": 140, "cz": 88, "radius": 10, "ct_value": 262.0},
]

SYSTEM_PROMPT = (
    "你是一名心血管影像分析专家，熟悉冠脉 CTA 钙化斑块与混合斑块的定量测量。"
    "你需要根据提供的 HU 统计特征，给出用于体积量化的分割阈值（HU）、显示窗宽窗位，"
    "并说明理由。只输出一个 JSON 对象，不要输出多余文字。"
)

USER_TEMPLATE = """病例信息与病灶 HU 统计特征如下，请据此给出量化参数。

病灶：{name}
病灶中心 HU（数据库记录）：{ct_value}
半径（体素）：{radius}
ROI 内 HU：均值 {roi_mean:.1f}，中位数 {roi_med:.1f}，P90 {roi_p90:.1f}，P95 {roi_p95:.1f}，P99 {roi_p99:.1f}，最大值 {roi_max:.1f}
背景（病灶外环）HU：均值 {bg_mean:.1f}，标准差 {bg_sd:.1f}
不同阈值下的分割体素数：130HU→{v130}，200HU→{v200}，250HU→{v250}，300HU→{v300}
扫描层厚名义 1 mm，体素等间距 1×1×1 mm，需按层厚合并做面积积分（Cavalieri 原理）。
已知存在部分容积效应：病灶边缘 HU 因层间平均而下降。

请输出 JSON，字段如下：
{{
  "threshold_hu": 整数，建议用于病灶分割的 HU 阈值,
  "window_center": 整数，建议显示窗位（HU）,
  "window_width": 整数，建议显示窗宽（HU）,
  "slice_thickness_mm": 整数，1-5，建议的重建/测量层厚,
  "reason": 不超过 120 字的理由，需说明阈值与病灶密度、背景噪声的关系
}}"""


def load_lesions(case_id):
    try:
        import pymysql
        from pymysql.cursors import DictCursor

        conn = pymysql.connect(
            host="127.0.0.1", port=3306, user="root", password="root",
            database="heart3d", charset="utf8mb4",
            cursorclass=DictCursor, connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, cx, cy, cz, radius, ct_value "
                "FROM lesions WHERE case_id=%s ORDER BY id", (case_id,)
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        if rows:
            for r in rows:
                r["radius"] = int(r["radius"])
                r["cx"] = int(r["cx"]); r["cy"] = int(r["cy"]); r["cz"] = int(r["cz"])
            return rows, "mysql:heart3d.lesions"
    except Exception as e:
        print("  [warn] MySQL 读取失败：%s" % e)
    return FALLBACK_LESIONS, "fallback:内置常量"


def collect_features(vol, les):
    cx, cy, cz, r = les["cx"], les["cy"], les["cz"], int(les["radius"])
    shape = vol.shape
    x0, x1 = max(0, cx - r - 8), min(shape[2], cx + r + 9)
    y0, y1 = max(0, cy - r - 8), min(shape[1], cy + r + 9)
    z0, z1 = max(0, cz - r - 8), min(shape[0], cz + r + 9)
    cube = np.asarray(vol[z0:z1, y0:y1, x0:x1], dtype=np.float32)

    zz, yy, xx = np.mgrid[z0:z1, y0:y1, x0:x1]
    dist = np.sqrt((zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2)
    core = cube[dist <= r]
    shell = cube[(dist > r + 4) & (dist <= r + 10)]

    vals_at = {}
    for th in (130, 200, 250, 300):
        sub = np.asarray(vol[max(0, cz - r):min(shape[0], cz + r + 1),
                             max(0, cy - r):min(shape[1], cy + r + 1),
                             max(0, cx - r):min(shape[2], cx + r + 1)], dtype=np.float32)
        vals_at[th] = int((sub > th).sum())

    return {
        "id": les["id"], "name": les["name"], "radius": r, "ct_value": float(les.get("ct_value") or 0.0),
        "roi_mean": float(core.mean()), "roi_med": float(np.median(core)),
        "roi_p90": float(np.percentile(core, 90)), "roi_p95": float(np.percentile(core, 95)),
        "roi_p99": float(np.percentile(core, 99)), "roi_max": float(core.max()),
        "bg_mean": float(shell.mean()) if shell.size else 0.0,
        "bg_sd": float(shell.std()) if shell.size else 0.0,
        "v130": vals_at[130], "v200": vals_at[200], "v250": vals_at[250], "v300": vals_at[300],
    }


def build_user_prompt(f):
    return USER_TEMPLATE.format(**f)


def call_ark(cfg, messages, max_tokens=2000):
    base = cfg["base_url"].rstrip("/")
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": float(cfg.get("temperature", 0.2)),
        "max_tokens": max_tokens,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]},
        method="POST",
    )
    t0 = time.time()
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=int(cfg.get("timeout", 60))) as resp:
                obj = json.loads(resp.read().decode("utf-8", "replace"))
            content = obj.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content, obj.get("usage", {}), time.time() - t0
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300])
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, str(e)[:200])
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_err or "unknown")


def parse_json(text):
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"```(?:json)?", "", t).replace("`", "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


def sanity_check(params, f):
    """人工验证与修正：AI 阈值必须落在背景噪声之上、病灶峰值之下。"""
    notes = []
    corrected = False
    th = params.get("threshold_hu")
    try:
        th = int(th)
    except Exception:
        th = None

    lo = f["bg_mean"] + 10.0
    hi = f["roi_max"] * 0.9
    if th is None:
        th = int(round((lo + hi) / 2.0))
        notes.append("AI 未给出可用阈值，按背景均值与峰值中点设为 %d" % th)
        corrected = True
    elif th < lo:
        notes.append("AI 阈值 %d 低于背景均值+10（%.1f），存在过分割风险，修正为 130" % (th, lo))
        th = 130
        corrected = True
    elif th > hi:
        notes.append("AI 阈值 %d 高于病灶峰值 90%%（%.1f），会完全漏检，修正为 %d" % (th, hi, int(round((lo + hi) / 2.0))))
        th = int(round((lo + hi) / 2.0))
        corrected = True

    t = params.get("slice_thickness_mm", DEFAULT_THICKNESS)
    try:
        t = int(t)
    except Exception:
        t = DEFAULT_THICKNESS
    if t < 1 or t > 5:
        notes.append("AI 层厚 %s 越界，修正为 %d" % (params.get("slice_thickness_mm"), DEFAULT_THICKNESS))
        t = DEFAULT_THICKNESS
        corrected = True

    wc = params.get("window_center")
    ww = params.get("window_width")
    try:
        wc = int(wc); ww = int(ww)
    except Exception:
        notes.append("AI 未给出有效窗设置，沿用默认 40/400")
        wc, ww = 40, 400
        corrected = True

    return {"threshold_hu": th, "slice_thickness_mm": t, "window_center": wc, "window_width": ww,
            "reason": params.get("reason", ""), "corrected": corrected, "notes": notes}


def measure_volume(vol, cz, cy, cx, r, thickness, threshold, z_start):
    shape = vol.shape
    x0, x1 = max(0, cx - r - 4), min(shape[2], cx + r + 5)
    y0, y1 = max(0, cy - r - 4), min(shape[1], cy + r + 5)
    z_end = min(shape[0], cz + r + 1)
    z = max(0, int(round(z_start)))
    total = 0
    while z < z_end:
        hi = min(z + thickness, z_end)
        if hi <= z:
            break
        block = np.asarray(vol[z:hi, y0:y1, x0:x1], dtype=np.float32)
        if block.ndim == 2:
            block = block[None, :, :]
        total += int((block.mean(axis=0) > threshold).sum()) * (hi - z)
        z = hi
    return float(total)


def measure_repeated(vol, les, thickness, threshold, rng):
    r = int(les["radius"])
    truth = 4.0 / 3.0 * math.pi * r ** 3
    z_nominal = les["cz"] - math.ceil((r + 1) / thickness) * thickness
    vals = []
    for i in range(REPEATS):
        jitter = 0.0 if i == 0 else float(rng.normal(0.0, 1.0))
        vals.append(measure_volume(vol, les["cz"], les["cy"], les["cx"], r, thickness, threshold, z_nominal + jitter))
    mean_v = float(np.mean(vals))
    sd_v = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
    cv = sd_v / mean_v * 100.0 if mean_v else 0.0
    return {
        "measured_mean_mm3": round(mean_v, 3),
        "measured_sd_mm3": round(sd_v, 3),
        "truth_volume_mm3": round(truth, 3),
        "abs_error_mm3": round(mean_v - truth, 3),
        "rel_error_pct": round((mean_v - truth) / truth * 100.0, 3),
        "repeatability_cv_pct": round(cv, 3),
    }


def grid_best(summary_csv, lesion_id):
    """从 experiment_summary.csv 取该病灶穷举最优参数，作为参照组。"""
    import csv
    best = None
    with open(summary_csv, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if int(row["lesion_id"]) != lesion_id:
                continue
            err = abs(float(row["rel_error_mean_pct"]))
            if best is None or err < best[0]:
                best = (err, int(row["slice_thickness"]), int(row["threshold_hu"]), int(row["offset_nominal"]))
    return best


def main():
    rng = np.random.default_rng(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not CFG_PATH.exists():
        print("ERR: 缺少 %s" % CFG_PATH)
        return 2
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    if not cfg.get("api_key") or not cfg.get("model"):
        print("ERR: config.local.json 的 api_key / model 未填写完整")
        return 2

    vol = np.load(str(VOL_PATH), mmap_mode="r")
    lesions, src = load_lesions(CASE_ID)
    print("体数据 %s  shape=%s" % (VOL_PATH.name, vol.shape))
    print("病灶来源：%s" % src)

    features = [collect_features(vol, les) for les in lesions]
    with open(OUT_DIR / "features.json", "w", encoding="utf-8") as f:
        json.dump(features, f, ensure_ascii=False, indent=2)

    log_path = OUT_DIR / "ai_calls.jsonl"
    if log_path.exists():
        log_path.unlink()          # 每次运行重建台账，避免历史记录混入
    rows = []

    for f, les in zip(features, lesions):
        print("\n[病灶 %d] %s  r=%d  峰值=%.1f  背景=%.1f±%.1f" %
              (f["id"], f["name"], f["radius"], f["roi_max"], f["bg_mean"], f["bg_sd"]))
        user_prompt = build_user_prompt(f)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}]
        content, usage, elapsed = call_ark(cfg, messages)
        print("  AI 原始输出（%d 字，%.1fs）：%s" % (len(content), elapsed, content.replace("\n", " ")[:160]))

        parsed = parse_json(content)
        if parsed is None:
            parsed = {}
            print("  [warn] JSON 解析失败，进入人工修正流程")
        final = sanity_check(parsed, f)
        for n in final["notes"]:
            print("  修正：%s" % n)

        rec = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "case_id": CASE_ID,
            "lesion_id": f["id"],
            "lesion_name": f["name"],
            "model": cfg["model"],
            "user_prompt": user_prompt,
            "raw_response": content,
            "parsed": parsed,
            "final_params": {k: final[k] for k in ("threshold_hu", "slice_thickness_mm", "window_center", "window_width")},
            "corrected": final["corrected"],
            "notes": final["notes"],
            "usage": usage,
            "elapsed_sec": round(elapsed, 2),
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 三组对照：默认参数 / AI 参数 / 穷举最优
        groups = [
            ("default", DEFAULT_THICKNESS, DEFAULT_THRESHOLD, "临床默认（层厚3/阈值200）"),
            ("ai", final["slice_thickness_mm"], final["threshold_hu"],
             "AI 推荐" + ("（经人工修正）" if final["corrected"] else "")),
        ]
        best = grid_best(OUT_DIR / "experiment_summary.csv", f["id"])
        if best:
            groups.append(("grid_best", best[1], best[2], "穷举扫描最优（offset=%d）" % best[3]))

        for source, thickness, threshold, note in groups:
            m = measure_repeated(vol, les, thickness, threshold, rng)
            rows.append({
                "case_id": CASE_ID,
                "lesion_id": f["id"],
                "lesion_name": f["name"],
                "param_source": source,
                "note": note,
                "slice_thickness_mm": thickness,
                "threshold_hu": threshold,
                "window_center": final["window_center"] if source == "ai" else 40,
                "window_width": final["window_width"] if source == "ai" else 400,
                "truth_volume_mm3": m["truth_volume_mm3"],
                "measured_mean_mm3": m["measured_mean_mm3"],
                "measured_sd_mm3": m["measured_sd_mm3"],
                "abs_error_mm3": m["abs_error_mm3"],
                "rel_error_pct": m["rel_error_pct"],
                "repeatability_cv_pct": m["repeatability_cv_pct"],
                "corrected": final["corrected"] if source == "ai" else False,
            })
            print("  %-10s t=%d th=%d -> 测量 %.1f，相对误差 %+.2f%%，CV %.2f%%" %
                  (source, thickness, threshold, m["measured_mean_mm3"], m["rel_error_pct"],
                   m["repeatability_cv_pct"]))

    out_csv = OUT_DIR / "ai_vs_default.csv"
    import csv as _csv
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("\n对照表：%s（%d 行）" % (out_csv, len(rows)))
    print("调用记录：%s" % log_path)
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
