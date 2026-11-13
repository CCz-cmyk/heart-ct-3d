# -*- coding: utf-8 -*-
"""
AI4S 实验：剖切面重建参数对心脏 CT 病灶体积量化误差的影响

数据源：data/volumes/case_01.npy  （轴序 z, y, x；float32；HU）
病灶真值：数字体模按球体生成，真值体积 = 4/3*pi*r^3（体素，spacing 名义 1x1x1 mm）
自变量：层厚 t、分割阈值 HU、剖切起点偏移 offset
因变量：阈值分割面积积分得到的测量体积
指标：绝对误差、相对误差%、重复性 CV%

运行：backend\venv\Scripts\python.exe research\experiment.py
"""

import csv
import json
import math
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VOL_PATH = ROOT / "data" / "volumes" / "case_01.npy"
OUT_DIR = ROOT / "research" / "results"
FIG_DIR = ROOT / "research" / "figures"

CASE_ID = 1
SLICE_THICKNESS = [1, 2, 3, 4, 5]
THRESHOLDS = [150, 200, 250, 300, 350]
OFFSETS = [-6, -4, -2, 0, 2, 4, 6]
REPEATS = 5
JITTER_SIGMA = 1.0
SEED = 20261108

FALLBACK_LESIONS = [
    {"id": 1, "name": "前降支近段钙化斑块", "cx": 132, "cy": 152, "cz": 92, "radius": 12, "ct_value": 421.0},
    {"id": 2, "name": "右冠近段混合斑块", "cx": 92, "cy": 140, "cz": 88, "radius": 10, "ct_value": 262.0},
]


def load_lesions(case_id):
    """优先从 MySQL 读病灶表，读不到则回退内置常量（回退情况写入 meta）。"""
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
    return FALLBACK_LESIONS, "fallback:experiment.py 内置常量"


def measure_volume(vol, cz, cy, cx, r, thickness, threshold, z_start):
    """按层厚合并 -> 阈值分割 -> 面积按层厚累加，返回体素数（= mm^3，名义 spacing）。"""
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
        mean_img = block.mean(axis=0)          # 模拟厚层部分容积效应
        area = int((mean_img > threshold).sum())
        total += area * (hi - z)
        z = hi
    return float(total)


def main():
    t_all = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    if not VOL_PATH.exists():
        print("ERR: 体数据不存在 %s" % VOL_PATH)
        return 2

    vol = np.load(str(VOL_PATH), mmap_mode="r")
    print("体数据：%s" % VOL_PATH)
    print("  shape=%s dtype=%s HU范围=[%.1f, %.1f]" %
          (vol.shape, vol.dtype, float(vol.min()), float(vol.max())))

    lesions, src = load_lesions(CASE_ID)
    print("病灶来源：%s，共 %d 个" % (src, len(lesions)))

    rng = np.random.default_rng(SEED)
    detail_rows = []

    for les in lesions:
        cx, cy, cz = les["cx"], les["cy"], les["cz"]
        r = int(les["radius"])
        truth = 4.0 / 3.0 * math.pi * r ** 3
        print("  病灶 %s：中心(x,y,z)=(%d,%d,%d) r=%d 真值=%.1f mm3" %
              (les["name"], cx, cy, cz, r, truth))

        for thickness in SLICE_THICKNESS:
            for threshold in THRESHOLDS:
                for offset in OFFSETS:
                    for rep in range(REPEATS):
                        jitter = 0.0 if rep == 0 else float(rng.normal(0.0, JITTER_SIGMA))
                        # 切块以病灶中心对称对齐：块边界 = cz + k*thickness
                        z_base = cz - math.ceil((r + 1) / thickness) * thickness
                        z_start_actual = z_base + offset + jitter
                        measured = measure_volume(vol, cz, cy, cx, r, thickness, threshold, z_start_actual)
                        abs_err = measured - truth
                        rel_err = abs_err / truth * 100.0
                        detail_rows.append({
                            "case_id": CASE_ID,
                            "lesion_id": les["id"],
                            "lesion_name": les["name"],
                            "radius_vox": r,
                            "truth_volume_mm3": round(truth, 3),
                            "slice_thickness": thickness,
                            "threshold_hu": threshold,
                            "offset_nominal": offset,
                            "offset_actual": round(z_start_actual - (cz - r), 3),
                            "z_start_actual": round(z_start_actual, 3),
                            "repeat_idx": rep + 1,
                            "measured_volume_mm3": round(measured, 3),
                            "abs_error_mm3": round(abs_err, 3),
                            "rel_error_pct": round(rel_err, 3),
                        })

    detail_csv = OUT_DIR / "experiment_results.csv"
    fields = list(detail_rows[0].keys())
    with open(detail_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(detail_rows)
    print("明细：%s（%d 行）" % (detail_csv, len(detail_rows)))

    # 按（病灶 / 层厚 / 阈值 / 名义偏移）汇总：均值±SD、重复性 CV
    groups = {}
    for row in detail_rows:
        k = (row["lesion_id"], row["slice_thickness"], row["threshold_hu"], row["offset_nominal"])
        groups.setdefault(k, []).append(row)

    summary_rows = []
    for k, rows in sorted(groups.items()):
        lesion_id, thickness, threshold, offset = k
        truth = rows[0]["truth_volume_mm3"]
        vals = np.array([x["measured_volume_mm3"] for x in rows], dtype=np.float64)
        rels = np.array([x["rel_error_pct"] for x in rows], dtype=np.float64)
        mean_v = float(vals.mean())
        sd_v = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
        cv = sd_v / mean_v * 100.0 if mean_v else 0.0
        summary_rows.append({
            "case_id": CASE_ID,
            "lesion_id": lesion_id,
            "lesion_name": rows[0]["lesion_name"],
            "radius_vox": rows[0]["radius_vox"],
            "truth_volume_mm3": truth,
            "slice_thickness": thickness,
            "threshold_hu": threshold,
            "offset_nominal": offset,
            "n_repeats": len(rows),
            "measured_mean_mm3": round(mean_v, 3),
            "measured_sd_mm3": round(sd_v, 3),
            "repeatability_cv_pct": round(cv, 3),
            "rel_error_mean_pct": round(float(rels.mean()), 3),
            "rel_error_sd_pct": round(float(rels.std(ddof=1)) if len(rels) > 1 else 0.0, 3),
        })

    summary_csv = OUT_DIR / "experiment_summary.csv"
    with open(summary_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)
    print("汇总：%s（%d 组）" % (summary_csv, len(summary_rows)))

    # 关键结论：默认参数组与全局最优组
    def find(lesion_id=None, thickness=None, threshold=None, offset=None):
        cands = [r for r in summary_rows
                 if (lesion_id is None or r["lesion_id"] == lesion_id)
                 and (thickness is None or r["slice_thickness"] == thickness)
                 and (threshold is None or r["threshold_hu"] == threshold)
                 and (offset is None or r["offset_nominal"] == offset)]
        return cands

    baseline = find(thickness=1, threshold=200, offset=0)
    best = min(summary_rows, key=lambda r: abs(r["rel_error_mean_pct"]))
    worst = max(summary_rows, key=lambda r: abs(r["rel_error_mean_pct"]))

    print("基线（层厚1/阈值200/偏移0）：")
    for b in baseline:
        print("  lesion%d 测量=%.1f 真值=%.1f 相对误差=%+.2f%% CV=%.2f%%" %
              (b["lesion_id"], b["measured_mean_mm3"], b["truth_volume_mm3"],
               b["rel_error_mean_pct"], b["repeatability_cv_pct"]))
    print("最优组：lesion%d t=%d th=%d off=%d 相对误差=%+.2f%%" %
          (best["lesion_id"], best["slice_thickness"], best["threshold_hu"],
           best["offset_nominal"], best["rel_error_mean_pct"]))
    print("最差组：lesion%d t=%d th=%d off=%d 相对误差=%+.2f%%" %
          (worst["lesion_id"], worst["slice_thickness"], worst["threshold_hu"],
           worst["offset_nominal"], worst["rel_error_mean_pct"]))

    meta = {
        "title": "剖切面重建参数对病灶体积量化误差的影响",
        "case_id": CASE_ID,
        "volume_file": str(VOL_PATH),
        "volume_shape": list(vol.shape),
        "volume_dtype": str(vol.dtype),
        "hu_range": [float(vol.min()), float(vol.max())],
        "axis_order": "z, y, x（数据库病灶坐标为 x, y, z，已换算为 [cz, cy, cx]）",
        "spacing_mm": [1.0, 1.0, 1.0],
        "spacing_note": "名义等间距，未做物理标定；体积单位为体素数（mm^3 等价）",
        "truth_definition": "4/3*pi*r^3，r 为数字体模生成参数中的病灶半径",
        "lesion_source": src,
        "slice_thickness": SLICE_THICKNESS,
        "thresholds_hu": THRESHOLDS,
        "offsets_vox": OFFSETS,
        "repeats_per_condition": REPEATS,
        "jitter_sigma_vox": JITTER_SIGMA,
        "seed": SEED,
        "detail_rows": len(detail_rows),
        "summary_rows": len(summary_rows),
        "runtime_sec": round(time.time() - t_all, 2),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "platform": platform.platform(),
    }
    meta_path = OUT_DIR / "experiment_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("元信息：%s" % meta_path)

    print("DONE %.1fs" % (time.time() - t_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
