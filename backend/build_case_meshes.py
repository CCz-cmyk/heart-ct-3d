"""为所有病例预生成专属三维网格（缓存到 data/case_meshes）。

用法： backend\\venv\\Scripts\\python.exe backend\\build_case_meshes.py
"""
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from app import case_mesh, dicom_io  # noqa: E402
from app.config import DATA_DIR  # noqa: E402
from app.database import get_conn  # noqa: E402

MESH_DIR = DATA_DIR.parent / 'case_meshes'


def load_vol(vol_file: str) -> np.ndarray:
    npy = DATA_DIR / vol_file
    if npy.exists():
        return np.load(npy)
    stem = Path(vol_file).stem
    if dicom_io.has_series(vol_file):
        return dicom_io.read_volume(vol_file)[0]
    raise FileNotFoundError(vol_file)


def main():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id, patient_id, patient_name, vol_file FROM cases ORDER BY id')
            cases = cur.fetchall()
    finally:
        conn.close()

    MESH_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for c in cases:
        stem = Path(c['vol_file']).stem
        t0 = time.time()
        try:
            vol = load_vol(c['vol_file'])
            glb, meta = case_mesh.build_case_mesh(vol, MESH_DIR, stem)
        except Exception as exc:  # noqa: BLE001
            print('!! %-8s id=%-3s %s -> %s' % (c['patient_id'], c['id'], c['patient_name'], exc))
            continue
        mb = glb.stat().st_size / 1048576
        total += glb.stat().st_size
        print('OK %-8s id=%-3s %-6s %-12s %5.2f MB  faces=%d  %.2fs'
              % (c['patient_id'], c['id'], stem, c['patient_name'], mb,
                 sum(meta['faces'].values()), time.time() - t0))
    print('共 %d 例，合计 %.1f MB，目录 %s' % (len(cases), total / 1048576, MESH_DIR))


if __name__ == '__main__':
    main()
