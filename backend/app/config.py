from pathlib import Path

# 项目根目录: C:\heart_3d_project
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / 'data' / 'volumes'
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_HOST = '127.0.0.1'
DB_PORT = 3306
DB_USER = 'root'
DB_PASSWORD = 'root'
DB_NAME = 'heart3d'

SECRET_KEY = 'heart3d-demo-secret-2026'
TOKEN_EXPIRE = 24 * 3600  # 秒