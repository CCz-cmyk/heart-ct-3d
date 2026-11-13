"""模型凭证统一加载：环境变量优先，本地配置文件兜底。

为什么要这一层：
  - 密钥不该写死在代码里，也不该只存在于一个明文文件里；
  - 部署/演示机器可以用环境变量注入，仓库里只留 config.local.example.json；
  - 后端、研究脚本取凭证走同一个入口，避免出现「一处改了另一处还是旧 key」。

环境变量：
  ARK_API_KEY     必填（缺失则回退配置文件）
  ARK_MODEL       推理接入点 ID（ep-xxxx）或模型 ID
  ARK_BASE_URL    默认 https://ark.cn-beijing.volces.com/api/v3
  ARK_TIMEOUT     秒，默认 60
  ARK_TEMPERATURE 默认 0.2
"""
import json
import os
from pathlib import Path

CFG_PATH = Path(__file__).resolve().parent / 'config.local.json'
DEFAULT_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'

_ENV_KEYS = {
    'api_key': 'ARK_API_KEY',
    'model': 'ARK_MODEL',
    'base_url': 'ARK_BASE_URL',
    'provider': 'ARK_PROVIDER',
}


def load_cfg(require=True):
    """返回配置字典。require=False 时缺 key 也返回（供自检/体检使用）。"""
    cfg = {}
    if CFG_PATH.exists():
        try:
            cfg = json.loads(CFG_PATH.read_text(encoding='utf-8'))
        except Exception:
            cfg = {}
    # 环境变量覆盖文件值：部署时只配环境变量即可，不落盘
    for field, env_name in _ENV_KEYS.items():
        v = os.environ.get(env_name)
        if v:
            cfg[field] = v.strip()
    if os.environ.get('ARK_TIMEOUT'):
        try:
            cfg['timeout'] = int(os.environ['ARK_TIMEOUT'])
        except ValueError:
            pass
    if os.environ.get('ARK_TEMPERATURE'):
        try:
            cfg['temperature'] = float(os.environ['ARK_TEMPERATURE'])
        except ValueError:
            pass

    cfg.setdefault('base_url', DEFAULT_BASE_URL)
    cfg.setdefault('provider', 'volcengine_ark')
    cfg.setdefault('temperature', 0.2)
    cfg.setdefault('timeout', 60)

    # 候选接入点：config 里写 "models": ["ep-A", "ep-B"]，或用 ARK_MODELS 逗号分隔。
    # 数组顺序就是尝试顺序；某个接入点免费额度用尽（429）会自动换下一个，不必每次手改配置。
    env_models = os.environ.get('ARK_MODELS') or ''
    cand = [s.strip() for s in env_models.split(',') if s.strip()]
    cand += [str(s).strip() for s in (cfg.get('models') or []) if str(s).strip()]
    if cand:
        merged = cand                      # 显式给了候选列表，就按用户写的顺序来
    else:
        merged = [cfg['model']] if cfg.get('model') else []
    cfg['models'] = list(dict.fromkeys([m for m in merged if m]))
    if cfg['models']:
        cfg['model'] = cfg['models'][0]    # 对外展示的"主接入点"= 候选里第一个

    if require and (not cfg.get('api_key') or not cfg['models']):
        raise RuntimeError(
            '未配置模型凭证：设置环境变量 ARK_API_KEY / ARK_MODEL，'
            '或把 research/config.local.example.json 复制为 config.local.json 后填写'
        )
    return cfg


def mask_key(key):
    """只保留尾 4 位，用于日志与健康检查回显。"""
    k = str(key or '')
    return ('****' + k[-4:]) if len(k) >= 4 else '****'
