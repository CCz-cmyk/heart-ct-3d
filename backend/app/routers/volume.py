from fastapi import APIRouter
from fastapi.responses import Response
import numpy as np
from ..database import get_conn
from ..config import DATA_DIR

router = APIRouter()


@router.get('/volume/{vol_file}')
def raw_volume(vol_file: str):
    """按文件名读取体积二进制（调试/通用接口）。"""
    from pathlib import Path
    p = Path(vol_file)
    if p.name != vol_file:
        return Response('invalid', status_code=400)
    vol_path = DATA_DIR / p.name
    if not vol_path.exists():
        return Response('not found', status_code=404)
    arr = np.load(vol_path)
    raw = arr.astype('<f4').tobytes()
    resp = Response(content=raw, media_type='application/octet-stream')
    resp.headers['X-Vol-Dims'] = f'{arr.shape[2]},{arr.shape[1]},{arr.shape[0]}'
    return resp
