"""DICOM 序列读取：把影像目录（每层一个 .dcm）解析成三维体数据。

支持两种来源：
    data/dicom/<case>          —— 由 npy 体数据转换而来的标准 DICOM 序列
    data/dicom_upload/<case>   —— 用户通过接口上传的 DICOM zip 解包目录

对外接口：
    series_dir(vol_file) / upload_dir(case_id)
    has_series(vol_file)
    read_meta_dir(dir) / read_volume_dir(dir)
    read_meta(vol_file) / read_volume(vol_file)
"""
import hashlib
from pathlib import Path

import numpy as np
import pydicom

from .config import DATA_DIR

DICOM_ROOT = DATA_DIR.parent / 'dicom'
UPLOAD_ROOT = DATA_DIR.parent / 'dicom_upload'
CACHE_DIR = DATA_DIR.parent / 'dicom_cache'


def series_dir(vol_file: str) -> Path:
    """由 cases.vol_file（如 case_03.npy）推导 DICOM 序列目录。"""
    return DICOM_ROOT / Path(vol_file).stem


def upload_dir(case_id) -> Path:
    return UPLOAD_ROOT / ('case_%02d' % int(case_id))


def has_series(vol_file: str) -> bool:
    d = series_dir(vol_file)
    return d.is_dir() and any(d.glob('*.dcm'))


def has_upload(case_id) -> bool:
    d = upload_dir(case_id)
    return d.is_dir() and any(d.glob('*.dcm'))


def slice_files(d: Path):
    return sorted(d.glob('*.dcm'))


def read_meta_dir(d: Path) -> dict:
    """只读首片与末片，不加载全部像素。"""
    files = slice_files(d)
    if not files:
        return {'available': False}
    first = pydicom.dcmread(files[0], stop_before_pixels=True)
    last = pydicom.dcmread(files[-1], stop_before_pixels=True)
    return {
        'available': True,
        'source': 'dicom',
        'series_dir': str(d).replace('\\', '/'),
        'slice_count': len(files),
        'rows': int(first.Rows),
        'columns': int(first.Columns),
        'modality': str(first.Modality),
        'patient_id': str(first.PatientID),
        'patient_name': str(first.PatientName),
        'patient_sex': str(first.PatientSex),
        'patient_age': str(first.PatientAge),
        'study_date': str(first.StudyDate),
        'study_description': str(first.StudyDescription),
        'series_description': str(first.get('SeriesDescription', '')),
        'manufacturer': str(first.get('Manufacturer', '')),
        'institution': str(first.get('InstitutionName', '')),
        'series_instance_uid': str(first.SeriesInstanceUID),
        'study_instance_uid': str(first.StudyInstanceUID),
        'slice_thickness_mm': float(getattr(first, 'SliceThickness', 1.0) or 1.0),
        'pixel_spacing_mm': [float(v) for v in (getattr(first, 'PixelSpacing', [1.0, 1.0]) or [1.0, 1.0])],
        'window_center': float(getattr(first, 'WindowCenter', 120) or 120),
        'window_width': float(getattr(first, 'WindowWidth', 600) or 600),
        'bits_allocated': int(first.BitsAllocated),
        'pixel_representation': int(first.PixelRepresentation),
        'transfer_syntax': str(first.file_meta.TransferSyntaxUID),
        'first_position': [float(v) for v in getattr(first, 'ImagePositionPatient', [0, 0, 0])],
        'last_position': [float(v) for v in getattr(last, 'ImagePositionPatient', [0, 0, 0])],
        'file_size_mb': round(sum(f.stat().st_size for f in files) / 1024 / 1024, 1),
    }


def _cache_path(d: Path) -> Path:
    key = hashlib.md5(str(d).encode('utf-8')).hexdigest()[:12]
    return CACHE_DIR / ('%s_%s.npy' % (d.name, key))


def _cache_is_fresh(d: Path, cache: Path) -> bool:
    if not cache.exists():
        return False
    files = slice_files(d)
    if not files:
        return False
    return cache.stat().st_mtime >= max(f.stat().st_mtime for f in files)


def read_volume_dir(d: Path, use_cache: bool = True):
    """返回 (体数据 float32，顺序 z,y,x；元信息 dict)。首次解析后缓存为 npy。"""
    files = slice_files(d)
    if not files:
        raise FileNotFoundError('DICOM 序列不存在：%s' % d)

    meta = read_meta_dir(d)
    cache = _cache_path(d)
    if use_cache and _cache_is_fresh(d, cache):
        return np.load(cache), meta

    slices = []
    for f in files:
        ds = pydicom.dcmread(f)
        arr = ds.pixel_array.astype(np.float32)
        slope = float(getattr(ds, 'RescaleSlope', 1) or 1)
        intercept = float(getattr(ds, 'RescaleIntercept', 0) or 0)
        if slope != 1 or intercept != 0:
            arr = arr * slope + intercept
        slices.append(arr)
    vol = np.stack(slices, axis=0)  # (z, y, x)

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.save(cache, vol)
    return vol, meta


def read_meta(vol_file: str) -> dict:
    return read_meta_dir(series_dir(vol_file))


def read_volume(vol_file: str, use_cache: bool = True):
    return read_volume_dir(series_dir(vol_file), use_cache)
