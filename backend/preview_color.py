"""伪彩色剖面检查：心肌红、心腔蓝、冠脉/钙化白、肺灰、空气黑。"""
import numpy as np
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
VOL = ROOT / 'data' / 'volumes' / 'case_01.npy'
OUT = ROOT / 'data' / 'preview'
OUT.mkdir(parents=True, exist_ok=True)

arr = np.load(VOL)  # (Z,Y,X)


def color_map(v):
    """v: HU值数组 -> RGB uint8 (H,W,3)"""
    h, w = v.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    air = v < -900
    lung = (v >= -900) & (v < -100)
    chest = (v >= -100) & (v < 60)     # 胸腔软组织 -20
    blood = (v >= 60) & (v < 100)      # 心腔血液 80
    myocard = (v >= 100) & (v < 220)   # 心肌/血管壁 145-150
    high = v >= 220                    # 冠脉造影/钙化 350+
    rgb[air] = (10, 10, 12)
    rgb[lung] = (70, 70, 72)
    rgb[chest] = (140, 96, 60)
    rgb[blood] = (70, 120, 220)
    rgb[myocard] = (220, 60, 60)
    rgb[high] = (255, 255, 255)
    return rgb


def save_slice(slice2d, name, scale=2):
    rgb = color_map(slice2d)
    img = Image.fromarray(rgb, 'RGB').resize((slice2d.shape[1] * scale, slice2d.shape[0] * scale), Image.NEAREST)
    img.save(OUT / name)
    print(name, 'saved', img.size)


save_slice(arr[80, :, :], 'axial_z80_color.png')
save_slice(arr[88, :, :], 'axial_z88_color.png')
save_slice(arr[104, :, :], 'axial_z104_color.png')
# 冠状：行Z列X，翻转使上=头侧
save_slice(np.flipud(arr[:, 124, :]), 'coronal_y124_color.png')
save_slice(np.flipud(arr[:, 108, :]), 'coronal_y108_color.png')
# 矢状：行Z列Y，翻转使上=头侧
save_slice(np.flipud(arr[:, :, 120]), 'sagittal_x120_color.png')

# 心脏结构包围盒（>30 HU 且排除肺空气）
mask = arr > 30
zs, ys, xs = np.where(mask)
print('bbox (z,y,x): z[%d..%d] y[%d..%d] x[%d..%d]' % (zs.min(), zs.max(), ys.min(), ys.max(), xs.min(), xs.max()))
# 心肌+血+冠脉的重心
c = mask.sum()
print('center-of-mass: z=%.1f y=%.1f x=%.1f' % (zs.mean(), ys.mean(), xs.mean()))
# 心尖判断：下半(z<=88)区域质心应偏左前(x小,y小)
half = mask.copy()
half[89:, :, :] = False
zs2, ys2, xs2 = np.where(half)
print('apex-half(z<=88) com: y=%.1f x=%.1f' % (ys2.mean(), xs2.mean()))
