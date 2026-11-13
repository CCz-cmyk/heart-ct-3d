"""把 data/volumes 下的 npy 体数据转换为标准 DICOM 序列（CT 模态，逐层单帧）。

用法：
    venv\\Scripts\\python.exe make_dicom.py            # 全部病例
    venv\\Scripts\\python.exe make_dicom.py --case 1   # 指定单个病例
    venv\\Scripts\\python.exe make_dicom.py --limit 2  # 只做前 2 例（快速验证）

输出：data/dicom/<npy 文件名>/IM0001.dcm ... IM0192.dcm
      每个文件夹即一个 DICOM Series，任意 DICOM 查看器（RadiAnt / MicroDicom / 3D Slicer）可直接打开。
"""
import argparse
import ast
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from app.config import DATA_DIR  # noqa: E402

DICOM_DIR = DATA_DIR.parent / 'dicom'
SLICE_THICKNESS = 1.0
PIXEL_SPACING = [1.0, 1.0]
WINDOW_CENTER = 120
WINDOW_WIDTH = 600
STUDY_DATE_FALLBACK = '20260912'


def load_cases():
    """优先从数据库读病例元数据；数据库不可用时解析 seed_data.py 中的 CASES 常量。"""
    try:
        from app.database import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT id, patient_id, patient_name, gender, age, exam_date, '
                            'study_desc, vol_file FROM cases ORDER BY id')
                rows = cur.fetchall()
            if rows:
                return rows, 'database'
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print('[!] 数据库不可用，回退到 seed_data.py 常量：', exc)

    src = BACKEND / 'seed_data.py'
    tree = ast.parse(src.read_text(encoding='utf-8'))
    cases = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'CASES' for t in node.targets):
            for i, item in enumerate(ast.literal_eval(node.value), 1):
                cases.append({
                    'id': i,
                    'patient_id': item['patient_id'],
                    'patient_name': item['patient_name'],
                    'gender': item['gender'],
                    'age': item['age'],
                    'exam_date': item['exam_date'],
                    'study_desc': item['study_desc'],
                    'vol_file': 'case_%02d.npy' % i,
                })
            break
    return cases, 'seed_data.py'


def build_dataset(case, vol, z, uid_study, uid_series, frame_uid):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid(entropy_srcs=[uid_series, str(z)])
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid(entropy_srcs=['HEART3D-1.0'])

    ds = FileDataset('', {}, file_meta=meta, preamble=b'\0' * 128)

    ds.SpecificCharacterSet = 'ISO_IR 192'  # UTF-8，保证中文姓名正确
    ds.ImageType = ['ORIGINAL', 'PRIMARY', 'AXIAL']
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = uid_study
    ds.SeriesInstanceUID = uid_series
    ds.FrameOfReferenceUID = frame_uid

    # 患者
    ds.PatientName = case['patient_name']
    ds.PatientID = case['patient_id']
    ds.PatientSex = 'M' if '男' in str(case['gender']) else 'F'  # CS 只允许 M/F/O
    ds.PatientAge = '%03dY' % int(case['age'])

    # 检查 / 序列
    exam = str(case.get('exam_date') or '').replace('-', '')
    ds.StudyDate = exam if len(exam) == 8 else STUDY_DATE_FALLBACK
    ds.StudyTime = '093000'
    ds.StudyID = 'S' + str(case['patient_id'])
    ds.AccessionNumber = 'A' + str(case['patient_id'])
    ds.Modality = 'CT'
    ds.SeriesNumber = 1
    ds.InstanceNumber = z + 1
    ds.StudyDescription = case.get('study_desc') or '心脏CT平扫'
    ds.SeriesDescription = '%s - 心脏CT三维重建' % (case.get('study_desc') or '心脏CT平扫')

    # 设备 / 机构
    ds.Manufacturer = 'HEART3D Course Project'
    ds.InstitutionName = '软件工程课程设计实验室'
    ds.StationName = 'HEART3D-VIEWER'
    ds.SoftwareVersions = 'HEART3D 1.0'
    ds.ManufacturerModelName = 'Simulated Cardiac CT'
    ds.BodyPartExamined = 'HEART'
    ds.PatientPosition = 'HFS'

    # 几何
    ds.SliceThickness = SLICE_THICKNESS
    ds.PixelSpacing = PIXEL_SPACING
    ds.SliceLocation = float(z * SLICE_THICKNESS)
    ds.ImagePositionPatient = [0.0, 0.0, float(z * SLICE_THICKNESS)]
    ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.Rows = int(vol.shape[1])
    ds.Columns = int(vol.shape[2])
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1  # 有符号
    ds.RescaleIntercept = 0
    ds.RescaleSlope = 1
    ds.RescaleType = 'HU'
    ds.WindowCenter = WINDOW_CENTER
    ds.WindowWidth = WINDOW_WIDTH

    # 像素：HU 值直接写入 int16（体数据范围约 -1000 ~ 500，落在 int16 内）
    ds.PixelData = vol[z].astype('<i2').tobytes()
    return ds


def convert_case(case, out_root):
    src = DATA_DIR / case['vol_file']
    if not src.exists():
        print('  [-] 缺少体数据文件：', src)
        return 0, 0, 0
    vol = np.load(src)
    series_dir = out_root / src.stem
    series_dir.mkdir(parents=True, exist_ok=True)
    for old in series_dir.glob('*.dcm'):
        old.unlink()

    uid_study = generate_uid(entropy_srcs=['STUDY', str(case['patient_id'])])
    uid_series = generate_uid(entropy_srcs=['SERIES', str(case['patient_id'])])
    frame_uid = generate_uid(entropy_srcs=['FRAME', str(case['patient_id'])])

    n = vol.shape[0]
    for z in range(n):
        ds = build_dataset(case, vol, z, uid_study, uid_series, frame_uid)
        ds.save_as(series_dir / ('IM%04d.dcm' % (z + 1)), enforce_file_format=True)
    return n, int(vol.shape[1]), int(vol.shape[2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', type=int, default=0, help='只转换指定病例 id')
    ap.add_argument('--limit', type=int, default=0, help='只转换前 N 例')
    args = ap.parse_args()

    cases, source = load_cases()
    print('[*] 病例来源：%s，共 %d 例' % (source, len(cases)))
    if args.case:
        cases = [c for c in cases if int(c['id']) == args.case]
    if args.limit:
        cases = cases[:args.limit]

    DICOM_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    manifest = []
    for case in cases:
        print('[*] 转换 case %s（%s %s）...' % (case['id'], case['patient_id'], case['patient_name']))
        n, rows, cols = convert_case(case, DICOM_DIR)
        total += n
        if n:
            manifest.append({
                'case_id': int(case['id']),
                'patient_id': case['patient_id'],
                'patient_name': case['patient_name'],
                'series_dir': 'data/dicom/' + Path(case['vol_file']).stem,
                'slices': n,
                'rows': rows,
                'columns': cols,
                'slice_thickness_mm': SLICE_THICKNESS,
                'pixel_spacing_mm': PIXEL_SPACING,
                'created_at': datetime.now().isoformat(timespec='seconds'),
            })
        print('    + %d 片' % n)

    # 合并旧清单：只转换部分病例时，不能把其他病例的记录冲掉
    manifest_path = DICOM_DIR / 'manifest.json'
    merged = {}
    if manifest_path.exists():
        try:
            for m in json.loads(manifest_path.read_text(encoding='utf-8')):
                if 'case_id' in m:
                    merged[int(m['case_id'])] = m
        except Exception as exc:  # noqa: BLE001
            print('[!] 旧 manifest 解析失败，按新建处理：', exc)
    for m in manifest:
        merged[m['case_id']] = m
    manifest_path.write_text(
        json.dumps([merged[k] for k in sorted(merged)], ensure_ascii=False, indent=2),
        encoding='utf-8')
    size_mb = sum(f.stat().st_size for f in DICOM_DIR.rglob('*.dcm')) / 1024 / 1024
    print('[完成] 共 %d 片，%.1f MB，输出目录：%s' % (total, size_mb, DICOM_DIR))


if __name__ == '__main__':
    main()
