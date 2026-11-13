"""检查每个接入点现在还能不能用。额度用尽/接入点写错，一眼就能看出来。

用法：
    python check_models.py            # 只检查，不改配置
    python check_models.py --apply    # 把能用的自动排到前面并写回 config.local.json（先备份）
"""
import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CFG_PATH = Path(__file__).resolve().parent / 'config.local.json'

CODE_HINT = {
    429: '额度用尽（安心体验模式自动暂停）',
    404: '接入点不存在或没开通',
    401: 'key 无效',
    403: '无权限',
}


def main():
    cfg = json.loads(CFG_PATH.read_text(encoding='utf-8'))
    key = cfg.get('api_key') or ''
    if not key:
        print('配置里没有 api_key，先填 key')
        return 1
    models = cfg.get('models') or ([cfg['model']] if cfg.get('model') else [])
    if not models:
        print('配置里既没有 model 也没有 models')
        return 1
    url = cfg.get('base_url', 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/') + '/chat/completions'

    print(f'共 {len(models)} 个接入点，逐个实测（每次只花 4 个 token）：\n')
    usable = []
    for m in models:
        body = {'model': m, 'max_tokens': 4, 'temperature': 0,
                'messages': [{'role': 'user', 'content': 'hi'}]}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                json.loads(r.read().decode())
            print(f'  [可用] {m}')
            usable.append(m)
        except urllib.error.HTTPError as e:
            hint = CODE_HINT.get(e.code, '')
            print(f'  [不可用] {m}  →  HTTP {e.code} {hint}')
        except Exception as e:
            print(f'  [不可用] {m}  →  {type(e).__name__}: {e}')

    print()
    if not usable:
        print('没有一个能调通。要么去控制台关掉「安心体验模式」/开通模型，要么换 key。')
        return 1
    print(f'可用：{len(usable)} 个 →  建议把它排到第一位：{usable[0]}')

    if '--apply' in sys.argv:
        bak = CFG_PATH.with_name(f'config.local.bak{datetime.now():%Y%m%d_%H%M%S}.json')
        shutil.copy2(CFG_PATH, bak)
        cfg['models'] = usable + [m for m in models if m not in usable]
        cfg['model'] = cfg['models'][0]
        CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已写回配置，备份：{bak.name}')
    elif usable[0] != models[0]:
        print('想自动改顺序，加参数：python check_models.py --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
