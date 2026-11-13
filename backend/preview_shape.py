"""生成三个正交剖面的灰度预览图，用于检查模拟心脏形状。"""
import sys
from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
VOL = ROOT / 'data' / 'volumes' / 'case_01.npy'
OUT_DIR = ROOT / 'data' / 'preview'
OUT_DIR.mkdir(parents=True, exist_ok=True)

arr = np.load(VOL)  # (NZ, NY, NX) = (96,128,128)

# 窗宽窗位映射到 0-255
ww, wc = 700.0, 60.0
ct_min, ct_max = wc - ww / 2, wc + ww / 2


def to_img(slice2d):
    v = np.clip((slice2d - ct_min) / (ct_max - ct_min), 0, 1)
    return Image.fromarray((v * 255).astype(np.uint8))


# 轴位 (Z 层) - 横断面
to_img(arr[40, :, :]).transpose(Image.FLIP_TOP_BOTTOM).save(OUT_DIR / 'axial_z40.png')
to_img(arr[44, :, :]).transpose(Image.FLIP_TOP_BOTTOM).save(OUT_DIR / 'axial_z44.png')
# 冠状 (Y 层) - 前后
to_img(arr[:, 62, :]).transpose(Image.FLIP_TOP_BOTTOM).save(OUT_DIR / 'coronal_y62.png')
# 矢状 (X 层) - 左右
to_img(arr[:, :, 60]).transpose(Image.FLIP_TOP_BOTTOM).save(OUT_DIR / 'sagittal_x60.png')

# 统计各组织体素数
n_total = arr.size
n_air = int((arr < -900).sum())
n_lung = int(((arr >= -900) & (arr < -100)).sum())
n_soft = int(((arr >= -100) & (arr < 100)).sum())
n_heart = int(((arr >= 100) & (arr < 500)).sum())
print(f'total={n_total} air={n_air} lung={n_lung} soft={n_soft} heart_high={n_heart}')
print(f'min={arr.min():.1f} max={arr.max():.1f}')
for name in ('axial_z40.png', 'axial_z44.png', 'coronal_y62.png', 'sagittal_x60.png'):
    p = OUT_DIR / name
    print(name, p.exists(), p.stat().st_size if p.exists() else 0)
