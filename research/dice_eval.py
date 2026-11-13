# -*- coding: utf-8 -*-
"""在数字体模上计算分割掩膜与解析真值球的重叠度（Dice），评估层厚对边界的影响。

真值掩膜：距病灶中心 <= r 的体素（解析球）。
预测掩膜：按层厚 t 做块平均后 HU > 阈值的体素（与 Cavalieri 跳采同一口径）。
对象：case 1 病灶 1（前降支近段钙化斑块，整枚病灶均为钙化，真值掩膜与分割目标同义）。
"""

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(r"C:\heart_3d_project")
VOL = ROOT / "data" / "volumes" / "case_01.npy"
OUT = ROOT / "research" / "results" / "dice_vs_thickness.csv"


def load_lesions(case_id=1):
    import pymysql
    conn = pymysql.connect(host="127.0.0.1", port=3306, user="root", password="root",
                          database="heart3d", charset="utf8mb4")
    cur = conn.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT * FROM lesions WHERE case_id=%s AND source='manual' ORDER BY id", (case_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def predicted_mask(vol, cz, cy, cx, r, t, th):
    """按层厚 t 做块平均后阈值分割，返回 ROI 内的布尔掩膜。"""
    shape = vol.shape
    x0, x1 = max(0, cx - r - 5), min(shape[2], cx + r + 6)
    y0, y1 = max(0, cy - r - 5), min(shape[1], cy + r + 6)
    z0, z1 = max(0, cz - r - 2), min(shape[0], cz + r + 3)
    out = np.zeros((z1 - z0, y1 - y0, x1 - x0), dtype=bool)
    z = z0
    while z < z1:
        hi = min(z + t, z1)
        block = np.asarray(vol[z:hi, y0:y1, x0:x1], dtype=np.float32).mean(axis=0)
        out[z - z0:hi - z0] = block > th
        z = hi
    return out, (z0, y0, x0)


def truth_mask(cz, cy, cx, r, z0, y0, x0, z1, y1, x1):
    zz, yy, xx = np.mgrid[z0:z1, y0:y1, x0:x1]
    return ((zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2) <= r * r


def main():
    vol = np.load(str(VOL), mmap_mode="r")
    lesions = load_lesions(1)
    target = None
    for L in lesions:
        if int(np.round(L["ct_value"])) > 300:
            target = L
            break
    if target is None:
        raise SystemExit("未找到高密度病灶")
    cz, cy, cx = int(target["cz"]), int(target["cy"]), int(target["cx"])
    r = int(target["radius"])
    print("病灶 %s  r=%d  峰值 %.0f HU" % (target["name"], r, target["ct_value"]))

    rows = []
    for t in (1, 2, 3, 4, 5):
        for th in (150, 200, 250):
            pred, (z0, y0, x0) = predicted_mask(vol, cz, cy, cx, r, t, th)
            z1 = z0 + pred.shape[0]
            y1 = y0 + pred.shape[1]
            x1 = x0 + pred.shape[2]
            gt = truth_mask(cz, cy, cx, r, z0, y0, x0, z1, y1, x1)
            inter = int((pred & gt).sum())
            dice = 2.0 * inter / (int(pred.sum()) + int(gt.sum())) if (pred.sum() + gt.sum()) else 0.0
            vol_pred = int(pred.sum())
            vol_gt = int(gt.sum())
            rows.append({"thickness": t, "threshold": th,
                         "dice": round(dice, 4),
                         "pred_vox": vol_pred, "truth_vox": vol_gt,
                         "vol_ratio": round(vol_pred / vol_gt, 4) if vol_gt else 0.0})
            print("t=%d th=%d  Dice=%.3f  预测体素=%d 真值体素=%d  比值=%.3f" % (
                t, th, dice, vol_pred, vol_gt, (vol_pred / vol_gt if vol_gt else 0)))

    import csv
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("写出 %s" % OUT)


if __name__ == "__main__":
    main()
