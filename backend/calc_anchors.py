# -*- coding: utf-8 -*-
"""修正版：心尖取最尖端，主动脉弓取弓顶，各结构质心精确定位。"""
import numpy as np

v = np.load(r'C:\heart_3d_project\data\volumes\case_01.npy')
NZ, NY, NX = v.shape
Z = np.arange(NZ)[:, None, None]
Y = np.arange(NY)[None, :, None]
X = np.arange(NX)[None, None, :]

def com(mask):
    zs, ys, xs = np.where(mask)
    return [float(xs.mean()), float(ys.mean()), float(zs.mean())] if len(zs) else None

myo = v >= 140
blood = (v >= 70) & (v < 140)
coro = v >= 300

# 1) 心尖：心肌 z 最小的 3% 体素（最尖端锥部）
zs_myo = np.where(myo)[0]
z_lim = np.percentile(zs_myo, 3)
apex = myo & (Z < z_lim)
print('1 心尖   :', com(apex), 'z_lim=', round(float(z_lim), 1))

# 2) 左心室：血腔 z<88 左侧（含左室流出道）
lv = blood & (Z < 88)
print('2 左心室 :', com(lv))

# 3) 右心室：血腔 x>125 且 z<95
rv = blood & (Z < 95) & (X > 125)
print('3 右心室 :', com(rv))

# 4) 主动脉弓：血腔 z 最大的 10% 体素（弓顶）
zs_blood = np.where(blood)[0]
z_ao_lim = np.percentile(zs_blood, 90)
ao = blood & (Z >= z_ao_lim)
print('4 主动脉弓:', com(ao), 'z_lim=', round(float(z_ao_lim), 1))

# 5) 前降支：冠脉 z<80 前侧
lad = coro & (Z < 80) & (X < 130)
print('5 前降支 :', com(lad))

# 6) 左心房：血腔 z>=95 且 x<130
la = blood & (Z >= 95) & (X < 130)
print('6 左心房 :', com(la))

# 7) 右心房：血腔 z>=95 且 x>=130
ra = blood & (Z >= 95) & (X >= 130)
print('7 右心房 :', com(ra))
