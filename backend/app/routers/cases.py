import io
import json
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
import numpy as np
from ..database import get_conn
from ..routers.auth import require_user
from ..config import DATA_DIR
from .. import dicom_io

router = APIRouter()

MAX_UPLOAD_MB = 500
MAX_SLICES = 1200
MAX_DICOM_VOXELS = 96 * 1024 * 1024  # 上限约 384 MB float32，避免体绘制爆显存
AI_DIR = DATA_DIR.parent / 'ai_results'  # DATA_DIR 指向 data/volumes，存档在 data/ai_results


def _ai_lesion_count(case_id):
    """无人工标注的病例，病灶数取自最近一次 AI 分析存档；读不到返回 0。"""
    p = AI_DIR / f'case_{int(case_id):02d}.json'
    if not p.exists():
        return 0
    try:
        payload = json.loads(p.read_text(encoding='utf-8'))
        return len((payload.get('result') or {}).get('lesions') or [])
    except Exception:
        return 0


def _extract_dicom_zip(payload: bytes, tmp: Path) -> int:
    """把 zip 内的 DICOM 切片解到 tmp，按序重命名为 IM0001.dcm…，返回片数。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail='压缩包损坏或不是 zip 文件')

    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    count = 0
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(('.dcm', '.dicom', '.ima')):
            continue
        if count >= MAX_SLICES:
            break
        count += 1
        out = tmp / ('IM%04d.dcm' % count)
        with zf.open(info) as src, open(out, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    if count == 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=400, detail='压缩包内没有 .dcm 文件')
    return count


def _validate_slices(d: Path):
    """校验每片都能解析、且行列数一致，返回 (rows, columns, slices)。"""
    import pydicom

    files = sorted(d.glob('*.dcm'))
    if not files:
        raise HTTPException(status_code=400, detail='压缩包内没有 .dcm 文件')
    try:
        first = pydicom.dcmread(files[0], stop_before_pixels=True)
        for f in files:
            ds = pydicom.dcmread(f, stop_before_pixels=True)
            if (int(ds.Rows), int(ds.Columns)) != (int(first.Rows), int(first.Columns)):
                raise ValueError('切片尺寸不一致')
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(status_code=400, detail='DICOM 解析失败：%s' % exc)
    return int(first.Rows), int(first.Columns), len(files)


def _next_case_index() -> int:
    """分配新的 case_XX 序号：避开已有序列目录与体数据文件。"""
    root = dicom_io.DICOM_ROOT
    nums = []
    if root.is_dir():
        for d in root.iterdir():
            m = re.fullmatch(r'case_(\d+)', d.name)
            if m:
                nums.append(int(m.group(1)))
    idx = (max(nums) + 1) if nums else 1
    while (DATA_DIR / ('case_%02d.npy' % idx)).exists() or (root / ('case_%02d' % idx)).exists():
        idx += 1
    return idx


@router.post('/cases')
async def create_case(
    patient_id: str = Form(...),
    patient_name: str = Form(...),
    gender: str = Form('男'),
    age: int = Form(0),
    exam_date: str = Form(''),
    study_desc: str = Form(''),
    file: UploadFile = File(...),
    user=Depends(require_user),
):
    """上传 DICOM 序列（zip）新建病例档案：解包 → 校验 → 解析体数据 → 入库 → 可立即三维渲染。"""
    pid = (patient_id or '').strip()
    name = (patient_name or '').strip()
    if not pid or not name:
        raise HTTPException(status_code=400, detail='检查号与姓名不能为空')
    if not pid.isalnum():
        raise HTTPException(status_code=400, detail='检查号只能包含字母和数字')
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail='请上传 .zip 压缩包（内含逐层 .dcm 文件）')

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail='压缩包为空')
    if len(payload) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail='压缩包超过 %d MB' % MAX_UPLOAD_MB)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM cases WHERE patient_id=%s', (pid,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail='检查号 %s 已存在' % pid)
    finally:
        conn.close()

    idx = _next_case_index()
    vol_file = 'case_%02d.npy' % idx
    series = dicom_io.DICOM_ROOT / ('case_%02d' % idx)
    tmp = dicom_io.DICOM_ROOT / ('_new_%s' % uuid.uuid4().hex[:8])
    vol_path = DATA_DIR / vol_file
    new_id = None

    try:
        _extract_dicom_zip(payload, tmp)
        rows, cols, slices = _validate_slices(tmp)
        if rows * cols * slices > MAX_DICOM_VOXELS:
            raise HTTPException(
                status_code=400,
                detail='序列过大（%d×%d×%d），请先裁剪到较小范围再上传' % (cols, rows, slices))

        vol, meta = dicom_io.read_volume_dir(tmp, use_cache=False)
        np.save(vol_path, np.ascontiguousarray(vol, dtype=np.float32))

        if series.exists():
            shutil.rmtree(series)
        tmp.rename(series)

        dims = '%d,%d,%d' % (vol.shape[2], vol.shape[1], vol.shape[0])
        ps = [float(v) for v in (meta.get('pixel_spacing_mm') or [1.0, 1.0])]
        st = float(meta.get('slice_thickness_mm') or 1.0)
        spacing = '%.3f,%.3f,%.3f' % (ps[0], ps[1], st)
        date = (exam_date or '').strip() or datetime.now().strftime('%Y-%m-%d')
        desc = (study_desc or '').strip() or 'DICOM 导入'

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    '''INSERT INTO cases (patient_id, patient_name, gender, age, exam_date,
                       study_desc, status, vol_file, vol_dims, spacing)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (pid, name, (gender or '男').strip(), int(age or 0), date, desc,
                     '待分析', vol_file, dims, spacing),
                )
                new_id = int(cur.lastrowid)
        finally:
            conn.close()
    except Exception:
        for p in (tmp, series):
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        if vol_path.exists():
            vol_path.unlink()
        raise

    # 追加到转换清单，保持 data/dicom 清单完整
    manifest_path = dicom_io.DICOM_ROOT / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
    except Exception:  # noqa: BLE001
        manifest = []
    manifest = [m for m in manifest if int(m.get('case_id', 0)) != new_id]
    manifest.append({
        'case_id': new_id,
        'patient_id': pid,
        'patient_name': name,
        'series_dir': 'data/dicom/case_%02d' % idx,
        'slices': slices,
        'rows': rows,
        'columns': cols,
        'slice_thickness_mm': st,
        'pixel_spacing_mm': ps,
        'created_at': datetime.now().isoformat(timespec='seconds'),
    })
    manifest.sort(key=lambda m: int(m.get('case_id', 0)))
    dicom_io.DICOM_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    return {
        'id': new_id,
        'patient_id': pid,
        'patient_name': name,
        'gender': (gender or '男').strip(),
        'age': int(age or 0),
        'exam_date': date,
        'study_desc': desc,
        'status': '待分析',
        'vol_file': vol_file,
        'vol_dims': dims,
        'spacing': spacing,
        'slices': slices,
        'rows': rows,
        'columns': cols,
        'dicom_available': True,
        'dicom_uploaded': False,
        'dicom': meta,
    }


class CaseUpdate(BaseModel):
    patient_id: str
    patient_name: str
    gender: str = '男'
    age: int = 0
    exam_date: str = ''
    study_desc: str = ''


@router.put('/cases/{case_id}')
def update_case(case_id: int, body: CaseUpdate, user=Depends(require_user)):
    """修改档案的患者信息（影像数据不动）。"""
    pid = (body.patient_id or '').strip()
    name = (body.patient_name or '').strip()
    if not pid or not name:
        raise HTTPException(status_code=400, detail='检查号与姓名不能为空')
    if not pid.isalnum():
        raise HTTPException(status_code=400, detail='检查号只能包含字母和数字')

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM cases WHERE id=%s', (case_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail='病例不存在')
            cur.execute('SELECT id FROM cases WHERE patient_id=%s AND id<>%s', (pid, case_id))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail='检查号 %s 已被其他档案占用' % pid)
            cur.execute(
                '''UPDATE cases SET patient_id=%s, patient_name=%s, gender=%s, age=%s,
                   exam_date=%s, study_desc=%s WHERE id=%s''',
                (pid, name, (body.gender or '男').strip(), int(body.age or 0),
                 (body.exam_date or '').strip(), (body.study_desc or '').strip(), case_id),
            )
            cur.execute('SELECT * FROM cases WHERE id=%s', (case_id,))
            row = cur.fetchone()
            cur.execute('SELECT COUNT(*) AS n FROM lesions WHERE case_id=%s', (case_id,))
            row['lesion_count'] = int(cur.fetchone()['n'])
    finally:
        conn.close()
    return row


@router.delete('/cases/{case_id}')
def delete_case(case_id: int, user=Depends(require_user)):
    """删除档案：病灶随外键级联删除，同时清理体数据、DICOM 序列与缓存。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM cases WHERE id=%s', (case_id,))
            case = cur.fetchone()
            if not case:
                raise HTTPException(status_code=404, detail='病例不存在')
            cur.execute('DELETE FROM cases WHERE id=%s', (case_id,))
    finally:
        conn.close()

    removed = []
    vol = case.get('vol_file') or ''
    if vol:
        npy = DATA_DIR / vol
        if npy.exists():
            npy.unlink()
            removed.append('volumes/' + npy.name)
        stem = Path(vol).stem
        for d in (dicom_io.DICOM_ROOT / stem, dicom_io.UPLOAD_ROOT / ('case_%02d' % case_id)):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        for c in dicom_io.CACHE_DIR.glob('%s_*.npy' % stem):
            c.unlink()
            removed.append('dicom_cache/' + c.name)

    manifest_path = dicom_io.DICOM_ROOT / 'manifest.json'
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest = [m for m in manifest if int(m.get('case_id', 0)) != int(case_id)]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:  # noqa: BLE001
            pass

    return {'deleted': int(case_id), 'patient_id': case.get('patient_id'), 'files': removed}


@router.get('/cases')
def list_cases(user=Depends(require_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                '''SELECT c.*, 
                          (SELECT COUNT(*) FROM lesions l WHERE l.case_id=c.id) AS lesion_count
                   FROM cases c ORDER BY c.id'''
            )
            rows = cur.fetchall()
        for r in rows:
            r['lesion_count'] = int(r.get('lesion_count') or 0)
            # 无人工标注时，用 AI 自动检出的条数填列表，否则新档案永远显示 0 个病灶
            if r['lesion_count'] == 0:
                r['lesion_count'] = _ai_lesion_count(r['id'])
                r['lesion_source'] = 'auto' if r['lesion_count'] else 'none'
            else:
                r['lesion_source'] = 'db'
            r['dicom_available'] = bool(r.get('vol_file') and dicom_io.has_series(r['vol_file']))
        return rows
    finally:
        conn.close()


@router.get('/cases/{case_id}')
def get_case(case_id: int, user=Depends(require_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM cases WHERE id=%s', (case_id,))
            case = cur.fetchone()
            cur.execute('SELECT * FROM lesions WHERE case_id=%s', (case_id,))
            lesions = cur.fetchall()
        if not case:
            raise HTTPException(status_code=404, detail='病例不存在')
        case['lesions'] = lesions
        case['dicom_available'] = bool(case.get('vol_file') and dicom_io.has_series(case['vol_file']))
        case['dicom_uploaded'] = dicom_io.has_upload(case_id)
        return case
    finally:
        conn.close()


def _case_vol_file(case_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT vol_file, vol_dims, spacing FROM cases WHERE id=%s', (case_id,))
            return cur.fetchone()
    finally:
        conn.close()


def _load_volume(case_id: int, source: str):
    case = _case_vol_file(case_id)
    if not case or not case.get('vol_file'):
        raise HTTPException(status_code=404, detail='病例不存在')

    vol_file = case['vol_file']
    src = (source or 'auto').lower()

    if src == 'upload':
        d = dicom_io.upload_dir(case_id)
        if not dicom_io.slice_files(d):
            raise HTTPException(status_code=404, detail='该病例还没有导入 DICOM 序列')
        return dicom_io.read_volume_dir(d)

    if src == 'auto':
        src = 'dicom' if dicom_io.has_series(vol_file) else 'npy'

    if src == 'dicom':
        if not dicom_io.has_series(vol_file):
            raise HTTPException(status_code=404, detail='该病例没有 DICOM 序列')
        return dicom_io.read_volume(vol_file)

    if src != 'npy':
        raise HTTPException(status_code=400, detail='source 只能是 auto / npy / dicom / upload')

    vol_path = DATA_DIR / vol_file
    if not vol_path.exists():
        raise HTTPException(status_code=404, detail='体积文件缺失')
    arr = np.load(vol_path)
    meta = {
        'available': True,
        'source': 'npy',
        'series_dir': 'data/volumes/' + vol_file,
        'slice_count': int(arr.shape[0]),
        'rows': int(arr.shape[1]),
        'columns': int(arr.shape[2]),
        'modality': 'NPY（程序化体数据）',
        'patient_id': '',
        'patient_name': '',
        'spacing': case.get('spacing'),
    }
    return arr, meta


@router.get('/cases/{case_id}/volume')
def get_volume(case_id: int, source: str = 'auto', user=Depends(require_user)):
    arr, meta = _load_volume(case_id, source)
    raw = np.ascontiguousarray(arr, dtype='<f4').tobytes()
    resp = Response(content=raw, media_type='application/octet-stream')
    resp.headers['X-Vol-Dims'] = '%d,%d,%d' % (arr.shape[2], arr.shape[1], arr.shape[0])
    if meta.get('source') in ('dicom', 'upload'):
        ps = meta.get('pixel_spacing_mm') or [1.0, 1.0]
        st = meta.get('slice_thickness_mm') or 1.0
        resp.headers['X-Vol-Spacing'] = '%.3f,%.3f,%.3f' % (ps[0], ps[1], st)
    else:
        # NPY 源：体素间距优先取病例记录（高清演示例为 0.5 mm），缺省按 1 mm
        sp = str(meta.get('spacing') or '').strip()
        resp.headers['X-Vol-Spacing'] = sp if sp.count(',') == 2 else '1.0,1.0,1.0'
    resp.headers['X-Vol-Source'] = meta.get('source', 'npy')
    resp.headers['X-Vol-Slices'] = str(meta.get('slice_count', arr.shape[0]))
    return resp


# ---------------------------------------------------------------- 病例专属三维网格
from fastapi.responses import FileResponse  # noqa: E402
from .. import case_mesh  # noqa: E402

MESH_DIR = DATA_DIR.parent / 'case_meshes'


def _mesh_stem(case_id: int):
    case = _case_vol_file(case_id)
    if not case or not case.get('vol_file'):
        raise HTTPException(status_code=404, detail='病例不存在')
    vol_file = case['vol_file']
    stem = Path(vol_file).stem
    npy = DATA_DIR / vol_file
    mtime = npy.stat().st_mtime if npy.exists() else 0
    return stem, mtime


def _ensure_case_mesh(case_id: int, source: str = 'auto'):
    """网格文件不存在或体数据更新过，才重新重建（缓存在 data/case_meshes）"""
    stem, mtime = _mesh_stem(case_id)
    glb = MESH_DIR / ('%s.glb' % stem)
    meta_path = MESH_DIR / ('%s.json' % stem)
    if glb.exists() and meta_path.exists() and glb.stat().st_mtime >= mtime:
        return glb, json.loads(meta_path.read_text(encoding='utf-8'))

    arr, _ = _load_volume(case_id, source)
    glb_path, meta = case_mesh.build_case_mesh(arr, MESH_DIR, stem)
    meta['case_id'] = case_id
    meta['source'] = source
    return glb_path, meta


@router.get('/cases/{case_id}/mesh')
def get_case_mesh(case_id: int, source: str = 'auto', user=Depends(require_user)):
    glb, meta = _ensure_case_mesh(case_id, source)
    return FileResponse(glb, media_type='model/gltf-binary', filename=glb.name,
                        headers={'X-Mesh-Faces': str(sum(meta['faces'].values())),
                                 'X-Mesh-Dims': ','.join(str(v) for v in meta['dims'])})


@router.get('/cases/{case_id}/mesh/meta')
def get_case_mesh_meta(case_id: int, source: str = 'auto', user=Depends(require_user)):
    _, meta = _ensure_case_mesh(case_id, source)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT name, cx, cy, cz, radius, ct_value, confidence, type '
                        "FROM lesions WHERE case_id=%s ORDER BY id", (case_id,))
            lesions = cur.fetchall()
    finally:
        conn.close()
    meta['lesions'] = lesions
    return meta



@router.get('/cases/{case_id}/dicom/info')
def dicom_info(case_id: int, source: str = 'dicom', user=Depends(require_user)):
    case = _case_vol_file(case_id)
    if not case or not case.get('vol_file'):
        raise HTTPException(status_code=404, detail='病例不存在')
    if source == 'upload':
        d = dicom_io.upload_dir(case_id)
        if not dicom_io.slice_files(d):
            return {'available': False, 'source': 'upload'}
        return dicom_io.read_meta_dir(d)
    if not dicom_io.has_series(case['vol_file']):
        return {'available': False, 'source': 'dicom', 'vol_file': case['vol_file']}
    meta = dicom_io.read_meta(case['vol_file'])
    meta['vol_dims'] = case.get('vol_dims')
    return meta


@router.get('/cases/{case_id}/dicom/file/{index}')
def dicom_file(case_id: int, index: int, user=Depends(require_user)):
    """下载单张 DICOM 切片，可直接用 DICOM 查看器打开。"""
    case = _case_vol_file(case_id)
    if not case or not case.get('vol_file'):
        raise HTTPException(status_code=404, detail='病例不存在')
    files = dicom_io.slice_files(dicom_io.series_dir(case['vol_file']))
    if not files or index < 1 or index > len(files):
        raise HTTPException(status_code=404, detail='切片序号超出范围')
    f = files[index - 1]
    return Response(content=f.read_bytes(), media_type='application/dicom',
                    headers={'Content-Disposition': 'attachment; filename="%s"' % f.name})


@router.get('/cases/{case_id}/dicom/series.zip')
def dicom_zip(case_id: int, source: str = 'dicom', user=Depends(require_user)):
    """打包下载整套 DICOM 序列（可直接导入 PACS / 3D Slicer）。"""
    case = _case_vol_file(case_id)
    if not case or not case.get('vol_file'):
        raise HTTPException(status_code=404, detail='病例不存在')
    d = dicom_io.upload_dir(case_id) if source == 'upload' else dicom_io.series_dir(case['vol_file'])
    files = dicom_io.slice_files(d)
    if not files:
        raise HTTPException(status_code=404, detail='该病例没有 DICOM 序列')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="%s_series.zip"' % Path(case['vol_file']).stem})


@router.post('/cases/{case_id}/dicom/upload')
async def dicom_upload(case_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """导入外部 DICOM：上传 zip（内含逐层 .dcm），解包校验后即可直接三维渲染。"""
    case = _case_vol_file(case_id)
    if not case:
        raise HTTPException(status_code=404, detail='病例不存在')
    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail='请上传 .zip 压缩包（内含逐层 .dcm 文件）')

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail='压缩包超过 %d MB' % MAX_UPLOAD_MB)

    target = dicom_io.upload_dir(case_id)
    tmp = target.with_suffix('.tmp')
    _extract_dicom_zip(payload, tmp)
    _validate_slices(tmp)

    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)

    meta = dicom_io.read_meta_dir(target)
    meta['uploaded'] = True
    return meta
