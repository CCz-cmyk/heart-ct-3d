import pymysql
from pymysql.cursors import DictCursor
from . import config

SCHEMA = '''
CREATE DATABASE IF NOT EXISTS {db} CHARACTER SET utf8mb4;
'''.format(db=config.DB_NAME)

TABLES = '''
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) UNIQUE NOT NULL,
  password_hash VARCHAR(128) NOT NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'doctor',
  real_name VARCHAR(64) DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cases (
  id INT AUTO_INCREMENT PRIMARY KEY,
  patient_id VARCHAR(32) UNIQUE NOT NULL,
  patient_name VARCHAR(64) NOT NULL,
  gender VARCHAR(8) DEFAULT '男',
  age INT DEFAULT 0,
  exam_date VARCHAR(32) DEFAULT '',
  study_desc VARCHAR(255) DEFAULT '',
  status VARCHAR(32) DEFAULT '待分析',
  vol_file VARCHAR(255) DEFAULT '',
  vol_dims VARCHAR(64) DEFAULT '',
  spacing VARCHAR(64) DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS lesions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  case_id INT NOT NULL,
  name VARCHAR(64) DEFAULT '',
  cx FLOAT DEFAULT 0,
  cy FLOAT DEFAULT 0,
  cz FLOAT DEFAULT 0,
  radius FLOAT DEFAULT 5,
  ct_value FLOAT DEFAULT 200,
  confidence FLOAT DEFAULT 0.9,
  type VARCHAR(32) DEFAULT '软组织病变',
  KEY idx_case (case_id),
  CONSTRAINT fk_lesions_case FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
'''


def get_conn(use_db=True):
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME if use_db else None,
        charset='utf8mb4',
        cursorclass=DictCursor,
        autocommit=True,
    )


def init_database():
    """创建数据库与全部数据表（幂等）"""
    conn = get_conn(use_db=False)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute('USE ' + config.DB_NAME)
            for stmt in TABLES.split(';'):
                s = stmt.strip()
                if s:
                    cur.execute(s + ';')
    finally:
        conn.close()
