"""模拟心脏 CT 断层体积数据生成器（高清版 256³）。

生成 256x256x192 的浮点 CT 值体数据（HU 单位），包含：
胸腔背景、双肺、斜置梨形心脏（左心室 / 右心室 / 心房）、
主动脉弓（含三分支）、肺动脉干（含分叉）、上/下腔静脉、
冠状动脉（LAD / RCA / LCX / D1 / OM / PDA）及预置病灶。
输出 numpy .npy 文件，供前端 VTK.js 直接加载渲染。

坐标约定：x = 左右，y = 前后，z = 上下（z 增大为头侧）。
心尖朝向左前下方，形成真实心脏的梨形/圆锥形轮廓。
"""
import numpy as np
from pathlib import Path

NX, NY, NZ = 256, 256, 192  # x(左右), y(前后), z(上下)

# 心脏中心与长轴方向（心尖朝左前下方）
CX, CY, CZ = 120.0, 124.0, 88.0
AX, AY, AZ = -0.45, -0.45, -0.75
HLEN = 80.0  # 心脏长轴半长


def _heart_radius(tt, r0=66.0):
    """长轴截面半径：心底圆钝、心尖强锥化，房室沟处有腰部凹陷。tt: 长轴投影坐标。"""
    u = np.clip(tt / HLEN, -1.0, 1.0)
    base = np.sqrt(np.maximum(0.0, 1.0 - u * u))
    shape = 1.0 - 0.6 * np.maximum(u, 0.0)                     # 心尖侧强锥化
    waist = 1.0 - 0.09 * np.exp(-((u + 0.28) / 0.18) ** 2)     # 房室沟腰部收窄
    return r0 * base * shape * waist + 0.6


def _tube(xx, yy, zz, pts, radius):
    """到曲线采样点的最小距离管道掩膜。"""
    d = np.sqrt(
        (xx[..., None] - pts[:, 0][None, None, None, :]) ** 2
        + (yy[..., None] - pts[:, 1][None, None, None, :]) ** 2
        + (zz[..., None] - pts[:, 2][None, None, None, :]) ** 2
    ).min(axis=-1)
    return d <= radius


def _surface_pts(u_vals, dvec):
    """沿心脏长轴取心肌表面采样点：P = C + u*HLEN*e + dvec*R_out(u)。
    半径取外表面的 1.02 倍，使冠脉略突出心肌表面（真实心脏冠脉走行于表面）。"""
    pts = []
    for u in u_vals:
        t = u * HLEN
        c = np.array([CX + t * AX, CY + t * AY, CZ + t * AZ], dtype=np.float32)
        r = _heart_radius(t) * 1.02
        pts.append(c + dvec * r)
    return np.array(pts, dtype=np.float32)


def generate_volume(lesions, out_path: Path):
    zz, yy, xx = np.mgrid[0:NZ, 0:NY, 0:NX].astype(np.float32)
    vol = np.full((NZ, NY, NX), -1000.0, dtype=np.float32)  # 空气背景

    # 1) 胸腔轮廓（极低密度，3D体绘制中保持透明，避免遮挡心脏）
    d_body = np.sqrt(((xx - CX) / 112) ** 2 + ((yy - CY) / 92) ** 2 + ((zz - CZ) / 88) ** 2)
    vol = np.where(d_body <= 1.0, -20.0, vol)

    # 2) 双肺（低密度）
    for lx, ly, lz, ra, rb, rc in [(52, 112, 80, 34, 44, 52), (188, 112, 80, 34, 44, 52)]:
        d_lung = np.sqrt(((xx - lx) / ra) ** 2 + ((yy - ly) / rb) ** 2 + ((zz - lz) / rc) ** 2)
        vol = np.where(d_lung <= 1.0, -850.0, vol)

    # 3) 心脏主体：斜置梨形（长轴投影 + 垂直距离）
    dx, dy, dz = xx - CX, yy - CY, zz - CZ
    tt = dx * AX + dy * AY + dz * AZ
    px, py, pz = tt * AX, tt * AY, tt * AZ
    rx, ry, rz = dx - px, dy - py, dz - pz
    r_perp = np.sqrt(rx * rx + ry * ry + rz * rz)

    R_out = _heart_radius(tt)
    u_apex = np.clip(tt / HLEN, 0.0, 1.0)
    R_in = _heart_radius(tt) * 0.62 * (1.0 - 0.25 * u_apex)  # 心尖侧腔体收窄

    myocardium = (r_perp <= R_out) & (r_perp > R_in)
    lv_cavity = r_perp <= R_in
    vol = np.where(myocardium, 150.0, vol)   # 心肌（高密度，3D高亮）
    vol = np.where(lv_cavity, 80.0, vol)     # 左室血液

    # 4) 右心室：贴附左室前外侧的新月形腔（薄壁）
    rv_cx, rv_cy, rv_cz = 140.0, 108.0, 92.0
    d_rv = np.sqrt(((xx - rv_cx) / 26.0) ** 2 + ((yy - rv_cy) / 20.0) ** 2 + ((zz - rv_cz) / 28.0) ** 2)
    rv_exclude = r_perp <= R_in          # 剔除左室腔与室间隔区域
    rv_cavity = (d_rv <= 1.0) & (~rv_exclude)
    rv_wall = (d_rv > 1.0) & (d_rv <= 1.30) & (~rv_exclude)
    vol = np.where(rv_cavity, 80.0, vol)
    vol = np.where(rv_wall, 145.0, vol)

    # 5) 心房（心底后上方：左房 + 右房）
    for ax_, ay_, az_, arx, ary, arz in [
        (CX - 20.0, CY + 20.0, CZ + 30.0, 18.0, 16.0, 20.0),  # 左房
        (CX + 20.0, CY + 24.0, CZ + 34.0, 18.0, 16.0, 20.0),  # 右房
    ]:
        d_at = np.sqrt(((xx - ax_) / arx) ** 2 + ((yy - ay_) / ary) ** 2 + ((zz - az_) / arz) ** 2)
        vol = np.where(d_at <= 1.0, 80.0, vol)
        vol = np.where((d_at > 1.0) & (d_at <= 1.28), 145.0, vol)

    # 5b) 左/右心耳（心底前上方肌性突起，解剖位置在左/右心房前外侧）
    for ax_, ay_, az_, arx, ary, arz in [
        (96.0, 136.0, 100.0, 10.0, 9.0, 11.0),   # 左心耳（左房前外侧）
        (148.0, 136.0, 106.0, 10.0, 9.0, 11.0),   # 右心耳（右房前外侧）
    ]:
        d_aur = np.sqrt(((xx - ax_) / arx) ** 2 + ((yy - ay_) / ary) ** 2 + ((zz - az_) / arz) ** 2)
        vol = np.where(d_aur <= 1.0, 150.0, vol)

    # 5c) 乳头肌：左室内壁肌性突起（增强心腔内部细节）
    for px_, py_, pz_, pr in [(114.0, 110.0, 56.0, 4.8), (104.0, 96.0, 48.0, 4.4), (122.0, 120.0, 68.0, 4.0)]:
        d_pap = np.sqrt(((xx - px_) / pr) ** 2 + ((yy - py_) / pr) ** 2 + ((zz - pz_) / pr) ** 2)
        vol = np.where((d_pap <= 1.0) & (lv_cavity), 150.0, vol)

    # 6) 主动脉：升主动脉 - 主动脉弓 - 降主动脉
    ao = []
    for z in np.linspace(112.0, 142.0, 8):
        ao.append((132.0, 116.0, z))
    for t in np.linspace(0.0, 1.0, 12):
        ao.append((132.0 - 34.0 * t, 116.0 + 16.0 * t, 142.0 - 4.0 * t))
    for z in np.linspace(138.0, 36.0, 14):
        ao.append((98.0, 132.0, z))
    ao = np.array(ao, dtype=np.float32)
    d_ao = np.sqrt(
        (xx[..., None] - ao[:, 0][None, None, None, :]) ** 2
        + (yy[..., None] - ao[:, 1][None, None, None, :]) ** 2
        + (zz[..., None] - ao[:, 2][None, None, None, :]) ** 2
    ).min(axis=-1)
    vol = np.where(d_ao <= 14.4, 80.0, vol)
    vol = np.where((d_ao > 14.4) & (d_ao <= 17.2), 145.0, vol)

    # 6b) 主动脉弓三分支：头臂干 / 左颈总动脉 / 左锁骨下动脉（解剖图标志性细节）
    aob = []
    for t in np.linspace(0.0, 1.0, 8):
        aob.append((114.0 - 10.0 * t, 132.0 + 18.0 * t, 140.0 + 2.0 * t))   # 头臂干 → 右上方
    for t in np.linspace(0.0, 1.0, 8):
        aob.append((106.0 - 4.0 * t, 132.0 + 18.0 * t, 140.0 - 2.0 * t))   # 左颈总 → 正上方
    for t in np.linspace(0.0, 1.0, 8):
        aob.append((100.0 - 10.0 * t, 130.0 + 16.0 * t, 138.0 - 4.0 * t))   # 左锁骨下 → 左上方
    aob = np.array(aob, dtype=np.float32)
    d_aob = np.sqrt(
        (xx[..., None] - aob[:, 0][None, None, None, :]) ** 2
        + (yy[..., None] - aob[:, 1][None, None, None, :]) ** 2
        + (zz[..., None] - aob[:, 2][None, None, None, :]) ** 2
    ).min(axis=-1)
    vol = np.where(d_aob <= 6.4, 80.0, vol)
    vol = np.where((d_aob > 6.4) & (d_aob <= 8.8), 145.0, vol)

    # 6c) 上腔静脉 / 下腔静脉（右心房大血管，增强真实感）
    svc = np.array([(144.0, 148.0 + 16.0 * t, 122.0 + 12.0 * t) for t in np.linspace(0.0, 1.0, 10)], dtype=np.float32)
    ivc = np.array([(148.0, 116.0 - 16.0 * t, 108.0 + 8.0 * t) for t in np.linspace(0.0, 1.0, 9)], dtype=np.float32)
    for seg, r in ((svc, 8.0), (ivc, 8.0)):
        d_ven = np.sqrt(
            (xx[..., None] - seg[:, 0][None, None, None, :]) ** 2
            + (yy[..., None] - seg[:, 1][None, None, None, :]) ** 2
            + (zz[..., None] - seg[:, 2][None, None, None, :]) ** 2
        ).min(axis=-1)
        vol = np.where(d_ven <= r, 80.0, vol)
        vol = np.where((d_ven > r) & (d_ven <= r * 1.35), 145.0, vol)

    # 7) 肺动脉干（右室流出道向上）
    pa = np.array([(124, 100, 116), (116, 94, 132), (106, 98, 144), (94, 106, 152)], dtype=np.float32)
    d_pa = np.sqrt(
        (xx[..., None] - pa[:, 0][None, None, None, :]) ** 2
        + (yy[..., None] - pa[:, 1][None, None, None, :]) ** 2
        + (zz[..., None] - pa[:, 2][None, None, None, :]) ** 2
    ).min(axis=-1)
    vol = np.where(d_pa <= 12.0, 80.0, vol)
    vol = np.where((d_pa > 12.0) & (d_pa <= 14.8), 145.0, vol)

    # 7b) 肺动脉分叉：左肺动脉 / 右肺动脉（肺门细节）
    pa_b = []
    for t in np.linspace(0.0, 1.0, 8):
        pa_b.append((94.0 - 22.0 * t, 106.0 - 10.0 * t, 152.0 + 8.0 * t))   # 左肺动脉 → 左上
    for t in np.linspace(0.0, 1.0, 8):
        pa_b.append((94.0 + 22.0 * t, 106.0 + 6.0 * t, 152.0 + 8.0 * t))   # 右肺动脉 → 右上
    pa_b = np.array(pa_b, dtype=np.float32)
    d_pab = np.sqrt(
        (xx[..., None] - pa_b[:, 0][None, None, None, :]) ** 2
        + (yy[..., None] - pa_b[:, 1][None, None, None, :]) ** 2
        + (zz[..., None] - pa_b[:, 2][None, None, None, :]) ** 2
    ).min(axis=-1)
    vol = np.where(d_pab <= 6.0, 80.0, vol)
    vol = np.where((d_pab > 6.0) & (d_pab <= 8.4), 145.0, vol)

    # 8) 冠状动脉（LAD 前降支 / RCA 右冠 / LCX 回旋支 / D1 对角支，造影剂高亮）
    lad = _surface_pts(np.linspace(-0.55, 0.85, 16), np.array([-0.28, -0.85, -0.32]))
    rca = _surface_pts(np.linspace(-0.50, 0.55, 12), np.array([0.82, -0.25, -0.45]))
    lcx = _surface_pts(np.linspace(-0.55, 0.45, 12), np.array([-0.78, -0.12, 0.42]))
    diag = _surface_pts(np.linspace(0.00, 0.38, 9), np.array([-0.62, -0.62, -0.15]))
    for seg in (lad, rca, lcx):
        vol = np.where(_tube(xx, yy, zz, seg, 6.8), 350.0, vol)
    vol = np.where(_tube(xx, yy, zz, diag, 5.6), 350.0, vol)

    # 8b) 冠脉补充分支：钝缘支 OM（左缘）/ 后降支 PDA（后室间沟）
    om = _surface_pts(np.linspace(-0.55, 0.35, 11), np.array([-0.78, -0.42, 0.46]))
    pda = _surface_pts(np.linspace(-0.30, 0.60, 10), np.array([0.25, -0.55, 0.80]))
    vol = np.where(_tube(xx, yy, zz, om, 5.2), 350.0, vol)
    vol = np.where(_tube(xx, yy, zz, pda, 5.2), 350.0, vol)

    # 9) 预置病灶
    for L in lesions:
        d_les = np.sqrt(
            ((xx - L['cx']) / L['radius']) ** 2
            + ((yy - L['cy']) / L['radius']) ** 2
            + ((zz - L['cz']) / L['radius']) ** 2
        )
        vol = np.where(d_les <= 1.0, L['ct_value'], vol)

    # 10) 软组织轻微噪声（真实感）
    noise = np.random.default_rng(42).normal(0, 3.0, vol.shape).astype(np.float32)
    vol = np.where(vol > -900, vol + noise, vol)
    vol = np.clip(vol, -1024.0, 2000.0).astype(np.float32)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_path, vol)
    return (NX, NY, NZ)
