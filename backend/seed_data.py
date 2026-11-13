"""初始化数据库：建库建表、写入管理员账号、10个模拟病例及病灶、生成体积数据。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import init_database, get_conn
from app.auth import hash_password
from app.volume_gen import generate_volume
from app.config import DATA_DIR

# 模拟病例数据：病例 + 病灶（网格坐标，需落在心脏区域）
CASES = [
    {
        'patient_id': 'P0001', 'patient_name': '张三', 'gender': '男', 'age': 58,
        'exam_date': '2026-09-12', 'study_desc': '冠状动脉CTA平扫',
        'lesions': [
            {'name': '前降支近段钙化斑块', 'cx': 132, 'cy': 152, 'cz': 92, 'radius': 12, 'ct_value': 420, 'confidence': 0.96, 'type': '钙化斑块'},
            {'name': '右冠近段混合斑块', 'cx': 92, 'cy': 140, 'cz': 88, 'radius': 10, 'ct_value': 260, 'confidence': 0.88, 'type': '软组织病变'},
        ],
    },
    {
        'patient_id': 'P0002', 'patient_name': '李四', 'gender': '女', 'age': 64,
        'exam_date': '2026-09-15', 'study_desc': '胸部增强CT',
        'lesions': [
            {'name': '左心室前壁低密度缺血灶', 'cx': 88, 'cy': 92, 'cz': 80, 'radius': 16, 'ct_value': 22, 'confidence': 0.91, 'type': '低密度缺血灶'},
        ],
    },
    {
        'patient_id': 'P0003', 'patient_name': '王五', 'gender': '男', 'age': 49,
        'exam_date': '2026-09-18', 'study_desc': '心脏CT平扫',
        'lesions': [
            {'name': '左心房附壁血栓', 'cx': 116, 'cy': 88, 'cz': 100, 'radius': 14, 'ct_value': 88, 'confidence': 0.84, 'type': '血栓'},
            {'name': '室间隔可疑占位', 'cx': 120, 'cy': 116, 'cz': 80, 'radius': 10, 'ct_value': 170, 'confidence': 0.76, 'type': '软组织病变'},
        ],
    },
    {
        'patient_id': 'P0004', 'patient_name': '赵六', 'gender': '男', 'age': 71,
        'exam_date': '2026-09-20', 'study_desc': '冠状动脉CTA',
        'lesions': [
            {'name': '左主干钙化', 'cx': 140, 'cy': 136, 'cz': 92, 'radius': 8, 'ct_value': 480, 'confidence': 0.97, 'type': '钙化斑块'},
            {'name': '回旋支钙化斑块', 'cx': 108, 'cy': 144, 'cz': 88, 'radius': 10, 'ct_value': 400, 'confidence': 0.93, 'type': '钙化斑块'},
            {'name': '右室壁低密度灶', 'cx': 92, 'cy': 104, 'cz': 76, 'radius': 12, 'ct_value': 18, 'confidence': 0.79, 'type': '低密度缺血灶'},
        ],
    },
    {
        'patient_id': 'P0005', 'patient_name': '孙七', 'gender': '女', 'age': 55,
        'exam_date': '2026-09-22', 'study_desc': '心脏CT平扫',
        'lesions': [
            {'name': '左心室侧壁软组织占位', 'cx': 144, 'cy': 100, 'cz': 84, 'radius': 14, 'ct_value': 185, 'confidence': 0.89, 'type': '软组织病变'},
        ],
    },
    {
        'patient_id': 'P0006', 'patient_name': '周八', 'gender': '男', 'age': 62,
        'exam_date': '2026-09-25', 'study_desc': '冠状动脉CTA平扫',
        'lesions': [
            {'name': '前降支中段钙化', 'cx': 132, 'cy': 156, 'cz': 84, 'radius': 10, 'ct_value': 440, 'confidence': 0.95, 'type': '钙化斑块'},
            {'name': '心尖部附壁血栓', 'cx': 176, 'cy': 68, 'cz': 72, 'radius': 12, 'ct_value': 92, 'confidence': 0.82, 'type': '血栓'},
        ],
    },
    {
        'patient_id': 'P0007', 'patient_name': '吴九', 'gender': '女', 'age': 47,
        'exam_date': '2026-09-28', 'study_desc': '胸部增强CT',
        'lesions': [
            {'name': '室间隔膜部低密度灶', 'cx': 120, 'cy': 112, 'cz': 92, 'radius': 10, 'ct_value': 25, 'confidence': 0.81, 'type': '低密度缺血灶'},
        ],
    },
    {
        'patient_id': 'P0008', 'patient_name': '郑十', 'gender': '男', 'age': 66,
        'exam_date': '2026-10-08', 'study_desc': '冠状动脉CTA',
        'lesions': [
            {'name': '右冠中段钙化斑块', 'cx': 84, 'cy': 148, 'cz': 84, 'radius': 12, 'ct_value': 410, 'confidence': 0.94, 'type': '钙化斑块'},
            {'name': '左心室后壁占位', 'cx': 104, 'cy': 72, 'cz': 84, 'radius': 14, 'ct_value': 190, 'confidence': 0.86, 'type': '软组织病变'},
            {'name': '左心房血栓', 'cx': 116, 'cy': 80, 'cz': 104, 'radius': 10, 'ct_value': 85, 'confidence': 0.77, 'type': '血栓'},
        ],
    },
    {
        'patient_id': 'P0009', 'patient_name': '陈十一', 'gender': '男', 'age': 53,
        'exam_date': '2026-10-12', 'study_desc': '心脏CT平扫',
        'lesions': [
            {'name': '前降支远段钙化', 'cx': 140, 'cy': 168, 'cz': 76, 'radius': 8, 'ct_value': 460, 'confidence': 0.92, 'type': '钙化斑块'},
        ],
    },
    {
        'patient_id': 'P0010', 'patient_name': '林十二', 'gender': '女', 'age': 59,
        'exam_date': '2026-10-16', 'study_desc': '冠状动脉CTA平扫',
        'lesions': [
            {'name': '回旋支近段混合斑块', 'cx': 112, 'cy': 140, 'cz': 92, 'radius': 10, 'ct_value': 240, 'confidence': 0.87, 'type': '软组织病变'},
            {'name': '心尖区低密度缺血灶', 'cx': 172, 'cy': 72, 'cz': 76, 'radius': 14, 'ct_value': 20, 'confidence': 0.83, 'type': '低密度缺血灶'},
        ],
    },
]


def seed():
    print('[*] 初始化数据库结构 ...')
    init_database()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 管理员账号 root/root
            cur.execute('SELECT COUNT(*) AS n FROM users WHERE username=%s', ('root',))
            if cur.fetchone()['n'] == 0:
                cur.execute(
                    'INSERT INTO users (username, password_hash, role, real_name) VALUES (%s,%s,%s,%s)',
                    ('root', hash_password('root'), 'admin', '系统管理员'),
                )
                print('    + 管理员账号 root / root 已创建')

            # 清空病例与病灶，重新写入（重置自增，保持 id 从 1 开始）
            cur.execute('DELETE FROM lesions')
            cur.execute('DELETE FROM cases')
            cur.execute('ALTER TABLE cases AUTO_INCREMENT = 1')
            cur.execute('ALTER TABLE lesions AUTO_INCREMENT = 1')

            for i, c in enumerate(CASES, 1):
                vol_file = f'case_{i:02d}.npy'
                dims = generate_volume(c['lesions'], DATA_DIR / vol_file)
                vol_dims = '%d,%d,%d' % (dims[0], dims[1], dims[2])
                cur.execute(
                    '''INSERT INTO cases (patient_id, patient_name, gender, age, exam_date, study_desc,
                       status, vol_file, vol_dims, spacing)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (c['patient_id'], c['patient_name'], c['gender'], c['age'],
                     c['exam_date'], c['study_desc'], '待分析', vol_file, vol_dims, '1.0,1.0,1.0'),
                )
                case_id = cur.lastrowid
                for L in c['lesions']:
                    cur.execute(
                        '''INSERT INTO lesions (case_id, name, cx, cy, cz, radius, ct_value, confidence, type)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (case_id, L['name'], L['cx'], L['cy'], L['cz'],
                         L['radius'], L['ct_value'], L['confidence'], L['type']),
                    )
                # 体积数据已在上面生成（含病灶）
                print(f'    + 病例 {c["patient_id"]} {c["patient_name"]}：病灶 {len(c["lesions"])} 处，体积已生成')

        print('[*] 初始化完成：10个病例，共', sum(len(c['lesions']) for c in CASES), '处病灶')
        print('[!] 体数据已重建，请同步重建 DICOM 序列：')
        print('    backend\\venv\\Scripts\\python.exe backend\\make_dicom.py')
    finally:
        conn.close()


if __name__ == '__main__':
    seed()
