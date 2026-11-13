"""病例专属三维网格：按病例体数据做等值面重建，导出 GLB（体素坐标系）。

三层：
  heart     心肌 / 心外膜（平滑后 level=110）
  pool      心腔 + 大血管血池（band 70~110）
  coronary  冠状动脉（level=300，造影剂高密度）

顶点坐标保持体素坐标 (x, y, z)，前端统一做居中缩放，
病灶（cx, cy, cz 亦为体素坐标）因此可以直接落在模型上。
"""
from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import numpy as np

SIGMA = 2.0
STEP = 2
SMOOTH_ITERS = 2
LEVEL_HEART = 110.0
LEVEL_COR = 300.0
POOL_BAND = (70.0, 110.0)

PART_DEFS = [
    ("heart", (0.82, 0.74, 0.66), 1.0),
    ("pool", (0.70, 0.16, 0.18), 1.0),
    ("coronary", (0.95, 0.42, 0.32), 1.0),
]


# ---------------------------------------------------------------- 等值面
def _surf(vol, level, step=STEP):
    from skimage import measure

    verts, faces, normals, _ = measure.marching_cubes(
        vol, level=level, step_size=step, allow_degenerate=False
    )
    # marching_cubes 输出顺序 (i, j, k) = (z, y, x)，转成 (x, y, z)
    return (
        np.ascontiguousarray(verts[:, ::-1], dtype="<f4"),
        np.ascontiguousarray(faces, dtype="<u4"),
        np.ascontiguousarray(normals[:, ::-1], dtype="<f4"),
    )


def _smooth_verts(pos, faces, iters=2, lam=0.45):
    """Taubin 式顶点平滑，消除体素台阶；平滑后重算顶点法线"""
    if iters <= 0:
        return pos, _vnormals(pos, faces)
    a = faces[:, [0, 1]]
    b = faces[:, [1, 2]]
    c = faces[:, [2, 0]]
    edges = np.concatenate([a, b, c, a[:, ::-1], b[:, ::-1], c[:, ::-1]], axis=0)
    edges = np.unique(edges, axis=0)
    src, dst = edges[:, 0], edges[:, 1]
    n = len(pos)
    pos = pos.copy()
    for _ in range(iters):
        acc = np.zeros_like(pos)
        cnt = np.zeros(n, dtype=np.float32)
        np.add.at(acc, src, pos[dst])
        np.add.at(cnt, src, 1.0)
        avg = acc / np.maximum(cnt[:, None], 1.0)
        pos = pos * (1.0 - lam) + avg * lam
    return pos, _vnormals(pos, faces)


def _vnormals(pos, faces):
    fn = np.cross(pos[faces[:, 1]] - pos[faces[:, 0]], pos[faces[:, 2]] - pos[faces[:, 0]])
    vn = np.zeros_like(pos)
    np.add.at(vn, faces[:, 0], fn)
    np.add.at(vn, faces[:, 1], fn)
    np.add.at(vn, faces[:, 2], fn)
    ln = np.linalg.norm(vn, axis=1, keepdims=True)
    return np.ascontiguousarray(vn / np.maximum(ln, 1e-9), dtype="<f4")


def build_parts(vol: np.ndarray):
    from scipy.ndimage import binary_opening, gaussian_filter, label

    smooth = gaussian_filter(vol.astype(np.float32), SIGMA)

    # 心肌 / 心外膜：只保留 >=110 的组织（心肌 150、室壁 145、冠脉 350）
    heart = _surf(smooth, LEVEL_HEART)

    # 血池：70~110 区间。心外膜边界（空气→心肌）过渡带同样落在此区间，
    # 会在心脏表面结出一层薄壳，用开运算 + 最大连通域去掉，只留腔内血池。
    band_mask = (smooth >= POOL_BAND[0]) & (smooth < POOL_BAND[1])
    band_mask = binary_opening(band_mask, iterations=2)
    lab, num = label(band_mask)
    if num > 1:
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        band_mask = lab == int(sizes.argmax())
    pool = _surf(band_mask.astype(np.float32), 0.5, step=STEP)

    # 冠脉：造影剂高密度
    cor = _surf(smooth, LEVEL_COR)

    out = {}
    for key, part, it in (("heart", heart, SMOOTH_ITERS), ("pool", pool, SMOOTH_ITERS),
                          ("coronary", cor, 1)):
        pos, f, _ = part
        pos2, nrm = _smooth_verts(pos, f, iters=it)
        out[key] = (pos2, f, nrm)
    return out


# ---------------------------------------------------------------- GLB 写出
def _accessor(buffer_view, component_type, count, type_, minv=None, maxv=None):
    acc = {"bufferView": buffer_view, "componentType": component_type, "count": int(count), "type": type_}
    if minv is not None:
        acc["min"] = [float(x) for x in minv]
        acc["max"] = [float(x) for x in maxv]
    return acc


def write_glb(parts: dict, out_path: Path, name: str = "case"):
    buf = bytearray()
    views, accessors, meshes, materials, nodes = [], [], [], [], []

    def add_bytes(data: bytes, target: int) -> int:
        while len(buf) % 4:
            buf.append(0)
        off = len(buf)
        buf.extend(data)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": target})
        return len(views) - 1

    for i, (key, color, opacity) in enumerate(PART_DEFS):
        if key not in parts or parts[key][1].size == 0:
            continue
        pos, idx, nrm = parts[key]
        vi_pos = add_bytes(pos.tobytes(), 34962)
        vi_nrm = add_bytes(nrm.tobytes(), 34962)
        vi_idx = add_bytes(idx.reshape(-1).tobytes(), 34963)

        ai_pos = len(accessors)
        accessors.append(
            _accessor(
                vi_pos, 5126, len(pos), "VEC3",
                pos.min(axis=0).tolist(), pos.max(axis=0).tolist(),
            )
        )
        ai_nrm = len(accessors)
        accessors.append(_accessor(vi_nrm, 5126, len(nrm), "VEC3"))
        ai_idx = len(accessors)
        accessors.append(_accessor(vi_idx, 5125, idx.size, "SCALAR"))

        mat = {
            "name": f"mat_{key}",
            "pbrMetallicRoughness": {
                "baseColorFactor": [round(c, 3) for c in color] + [1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.62,
            },
            "doubleSided": True,
        }
        if opacity < 1.0:
            mat["alphaMode"] = "BLEND"
            mat["pbrMetallicRoughness"]["baseColorFactor"][3] = round(opacity, 3)
        else:
            mat["alphaMode"] = "OPAQUE"
        materials.append(mat)

        meshes.append({
            "name": f"mesh_{key}",
            "primitives": [{
                "attributes": {"POSITION": ai_pos, "NORMAL": ai_nrm},
                "indices": ai_idx,
                "material": i,
                "mode": 4,
            }],
        })
        nodes.append({"mesh": i, "name": key})

    gltf = {
        "asset": {"version": "2.0", "generator": "heart3d case_mesh"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes))), "name": name}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(buf)}],
    }
    gltf["scenes"][0]["nodes"] = list(range(len(nodes)))

    while len(buf) % 4:
        buf.append(0)
    gltf["buffers"][0]["byteLength"] = len(buf)

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "

    total = 12 + 8 + len(json_bytes) + 8 + len(buf)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        fh.write(json_bytes)
        fh.write(struct.pack("<II", len(buf), 0x004E4942))
        fh.write(bytes(buf))
    return total


# ---------------------------------------------------------------- 对外
def build_case_mesh(vol: np.ndarray, out_dir: Path, stem: str):
    """生成 <stem>.glb 与 <stem>.json，返回 (glb 路径, meta)"""
    t0 = time.time()
    parts = build_parts(vol)
    glb_path = out_dir / f"{stem}.glb"
    size = write_glb(parts, glb_path, stem)

    allv = np.concatenate([parts[k][0] for k in ("heart", "pool", "coronary") if parts[k][1].size])
    meta = {
        "stem": stem,
        "dims": [int(vol.shape[2]), int(vol.shape[1]), int(vol.shape[0])],  # nx, ny, nz
        "bbox": [allv.min(axis=0).round(2).tolist(), allv.max(axis=0).round(2).tolist()],
        "faces": {k: int(parts[k][1].shape[0]) for k in parts},
        "bytes": int(size),
        "build_sec": round(time.time() - t0, 2),
        "params": {"sigma": SIGMA, "step": STEP, "level_heart": LEVEL_HEART,
                   "level_coronary": LEVEL_COR, "pool_band": list(POOL_BAND)},
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return glb_path, meta
