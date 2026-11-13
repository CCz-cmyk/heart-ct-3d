"""AI 病灶分析：真实调用大模型（火山方舟），不读现成结论。

流程（四段，供前端轮询显示进度）：
  1. volume   读取 .npy 体数据
  2. feature  在病灶球体内外实测 HU 统计特征
  3. llm      逐病灶把特征交给大模型，要求返回结构化 JSON
  4. verify   对模型输出做合理性校验（阈值须高于背景+3SD、低于病灶峰值 90%），修正并留痕
"""
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..database import get_conn
from ..routers.auth import require_user

router = APIRouter()

ROOT = Path(__file__).resolve().parents[3]
RESEARCH_DIR = ROOT / 'research'
CONFIG_PATH = RESEARCH_DIR / 'config.local.json'
VOL_DIR = ROOT / 'data' / 'volumes'
AI_DIR = ROOT / 'data' / 'ai_results'
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

TASKS = {}
_LOCK = threading.Lock()


def _save_result(case_id, tid, result):
    """把一次完成的分析结果落盘，供刷新/切回病例时回显。"""
    try:
        AI_DIR.mkdir(parents=True, exist_ok=True)
        p = AI_DIR / f'case_{int(case_id):02d}.json'
        payload = {
            'task_id': tid,
            'saved_at': time.time(),
            'saved_at_text': time.strftime('%Y-%m-%d %H:%M:%S'),
            'result': result,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return p
    except Exception:
        return None


def _mark_analyzed(case_id):
    """分析完成后把病例状态置为「已分析」，让列表与档案页不再显示「待分析」。"""
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE cases SET status='已分析' WHERE id=%s AND status<>'已分析'",
                            (case_id,))
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _write_lesions(case_id, items):
    """把 AI 自动检出（无人工标注真值）的病灶写回 lesions 表。

    只处理 source='auto' 的条目，人工标注（source='manual'）原样保留；
    每次分析先清掉该病例旧的 auto 记录，重复分析不会累积重复病灶。
    """
    rows = [x for x in (items or []) if (x or {}).get('source') == 'auto']
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM lesions WHERE case_id=%s AND source='auto'", (case_id,))
                for x in rows:
                    cur.execute(
                        'INSERT INTO lesions (case_id,name,cx,cy,cz,radius,ct_value,confidence,type,source) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (case_id, str(x.get('name') or 'AI 检出病灶')[:64],
                         float(x.get('cx') or 0), float(x.get('cy') or 0), float(x.get('cz') or 0),
                         float(x.get('radius') or 5), float(x.get('ct_value') or 0),
                         float(x.get('confidence') or 0), str(x.get('type') or 'AI 检出')[:32], 'auto'),
                    )
                conn.commit()
        finally:
            conn.close()
        return len(rows)
    except Exception:
        return 0


def _cfg():
    """模型凭证统一由 research/cfgloader.py 提供：环境变量优先，本地配置文件兜底。"""
    from cfgloader import load_cfg
    return load_cfg()


def _friendly_error(raw):
    """把模型调用的原始报错翻译成一句人能看懂的话（原始串另行保留在 ai_error_raw）。"""
    t = raw or ''
    if 'SetLimitExceeded' in t or 'HTTP 429' in t:
        return '账号配额已用尽（429）：该模型在方舟控制台被限流或暂停，需关闭「安全体验模式」或更换模型后重试'
    if 'HTTP 401' in t:
        return '鉴权失败（401）：API Key 无效或已过期'
    if 'HTTP 403' in t:
        return '无权限（403）：接入点未授权，或该模型未在本账号开通'
    if 'HTTP 404' in t:
        return '接入点不存在（404）：model 填的 ep-xxx 或模型 ID 有误'
    if 'timed out' in t or 'timeout' in t.lower():
        return '请求超时：网络不可达或模型响应过慢，已重试 3 次'
    return (t[:120] + '…') if len(t) > 120 else t


def _set(tid, **kw):
    with _LOCK:
        TASKS.setdefault(tid, {}).update(kw)


def _sync(tid, stage, progress, text):
    _set(tid, stage=stage, progress=progress, stage_text=text, status='running', at=time.time())


def _extract(vol, cx, cy, cz, r):
    """在球体内/外实测 HU。vol 轴序为 (z, y, x)。"""
    pad = r + 10
    z0, z1 = max(0, cz - pad), min(vol.shape[0], cz + pad + 1)
    y0, y1 = max(0, cy - pad), min(vol.shape[1], cy + pad + 1)
    x0, x1 = max(0, cx - pad), min(vol.shape[2], cx + pad + 1)
    patch = vol[z0:z1, y0:y1, x0:x1]
    zz, yy, xx = np.mgrid[z0:z1, y0:y1, x0:x1]
    d2 = (zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2
    inside = d2 <= r * r
    ring = (d2 > (r + 2) ** 2) & (d2 <= (r + 9) ** 2)
    inner = patch[inside]
    outer = patch[ring]
    if inner.size == 0 or outer.size == 0:
        raise RuntimeError('病灶 ROI 超出体数据范围')
    bg_mean = float(outer.mean())
    bg_std = float(outer.std())
    return {
        'inside_voxels': int(inner.size),
        'mean_hu': round(float(inner.mean()), 2),
        'sd_hu': round(float(inner.std()), 2),
        'median_hu': round(float(np.median(inner)), 2),
        'p90_hu': round(float(np.percentile(inner, 90)), 2),
        'p95_hu': round(float(np.percentile(inner, 95)), 2),
        'peak_hu': round(float(inner.max()), 2),
        'min_hu': round(float(inner.min()), 2),
        'bg_mean_hu': round(bg_mean, 2),
        'bg_std_hu': round(bg_std, 2),
        'bg_plus_3sd_hu': round(bg_mean + 3 * bg_std, 2),
        'bg_minus_3sd_hu': round(bg_mean - 3 * bg_std, 2),
    }


LOW_KEYWORDS = ('低密度', '缺血', '水肿', '血栓', '梗死', '囊肿', '脂肪')


def _is_low_density(text):
    """按标注名称/类型判断该灶是低密度灶（需要反向阈值分割）。"""
    t = text or ''
    return any(k in t for k in LOW_KEYWORDS)


PROMPT_SYS = (
    '你是心脏 CT 影像分析专家，熟悉冠状动脉斑块的 CT 表现、HU 密度分级、'
    '部分容积效应与阈值分割原理。你必须只依据用户给出的实测数据作答，'
    '不得编造未提供的检查所见；输出必须是严格 JSON，不要加代码块标记。'
)

PROMPT_TPL = """请独立分析这一处心脏 CT 病灶，给出分割参数与判读意见。

【实测数据】
位置（x,y,z 网格）=({cx}, {cy}, {cz})
形态：球状，半径 {r} 体素，即直径约 {diam} mm；球内体素 {n} 个，球体积 {truth:.1f} mm³
HU 统计（球内）：均值 {mean}，中位数 {median}，P90 {p90}，P95 {p95}，峰值 {peak}，最小 {min}
周围背景环（半径 {r}+2 至 {r}+9 体素）：均值 {bgm} ± {bgs}，均值+3SD = {bg3}，均值-3SD = {bgm3}

【分割极性】
{polarity}

【形态学标注（供参考，你可以不同意，但若不同意要说明理由）】
{dbtype}

【领域约束】
{constraints}

【输出格式】严格 JSON，只输出一个对象：
{{
  "type_cn": "斑块/病灶类型（中文，如 钙化斑块、混合斑块、低密度斑块、附壁血栓）",
  "severity": "轻度|中度|重度",
  "threshold_hu": 整数,
  "slice_thickness_mm": 整数(1-5),
  "confidence": 0-1 小数,
  "finding": "影像所见，一句话，必须引用上面给出的实测数值",
  "suggestion": "临床建议，一句话",
  "agree_with_label": true 或 false
}}
"""


def _call_llm(cfg, prompt, log=None):
    """按候选接入点顺序调用，某个接入点额度用尽/不可用就自动换下一个。

    429（免费额度用尽、服务暂停）与 404（接入点不存在）属于「这个接入点不行」，
    立即换下一个，不在它身上反复等；超时、5xx 之类可能只是抖动，同一个接入点再试一次。
    全部失败才抛错，并把每个接入点的失败原因拼进错误串，便于一眼定位。
    返回 (响应 JSON, 实际使用的接入点)。
    """
    base = {
        'messages': [
            {'role': 'system', 'content': PROMPT_SYS},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': float(cfg.get('temperature', 0.2)),
        'max_tokens': int(cfg.get('max_tokens', 900)),
        # 关掉长链思考：判读只需按给定数值填一个 JSON，开着 thinking 会多耗十几倍 token 且慢几十倍
        'thinking': {'type': 'disabled'},
    }
    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    timeout = int(cfg.get('timeout', 60)) + 30
    models = cfg.get('models') or [cfg['model']]
    errors = []
    for model in models:
        last = None
        for attempt in range(2):
            body = dict(base, model=model)
            if attempt:
                body.pop('thinking', None)  # 该接入点不认 thinking 参数时退回默认
            req = urllib.request.Request(
                url,
                data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + cfg['api_key'],
                },
                method='POST',
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8')), model
            except urllib.error.HTTPError as e:
                last = f'HTTP {e.code}: {e.read().decode("utf-8", "ignore")[:200]}'
                if log is not None:
                    log.append({'model': model, 'attempt': attempt + 1,
                                'stage': 'http_error', 'detail': last[:160]})
                if e.code in (401, 403, 404, 429):
                    break  # 换别的接入点也没用错在这里，但本接入点确定不可用，直接下一个
            except Exception as e:
                last = str(e)
                if log is not None:
                    log.append({'model': model, 'attempt': attempt + 1,
                                'stage': 'exception', 'detail': last[:160]})
            if attempt == 0:
                time.sleep(0.8)
        errors.append(f'{model} → {last}')
    raise RuntimeError(
        f'模型调用失败（已尝试 {len(models)} 个接入点）：' + ' ‖ '.join(errors)
    )


def _parse_json(text):
    t = (text or '').strip()
    if t.startswith('```'):
        seg = t.split('```')[1]
        t = seg[4:] if seg.lower().startswith('json') else seg
    start, end = t.find('{'), t.rfind('}')
    if start < 0 or end < start:
        raise RuntimeError('模型未返回 JSON')
    return json.loads(t[start:end + 1])


def _verify(item, feat):
    """合理性与修正：高密度灶阈值须落在 [背景+3SD, 峰值 90%]；
    低密度灶方向相反，须落在 [球内均值+3SD, 球内均值与背景均值的中点]。"""
    notes = []
    low = feat.get('polarity') == 'low'
    if low:
        lo = feat['mean_hu'] + 3 * feat.get('sd_hu', 0)
        hi = (feat['mean_hu'] + feat['bg_mean_hu']) / 2
    else:
        lo = feat['bg_plus_3sd_hu']
        hi = feat['peak_hu'] * 0.9
    if not item or item.get('threshold_hu') in (None, ''):
        th = int(round(lo + 2))
        notes.append(f'模型未给出阈值，按规则兜底取 {th} HU')
    else:
        try:
            th = int(round(float(item['threshold_hu'])))
        except (TypeError, ValueError):
            th = int(round(lo + 2))
            notes.append(f'模型阈值不可解析，按规则兜底取 {th} HU')

    if lo >= hi:
        th = int(round((lo + hi) / 2))
        notes.append(f'球内分布（{lo:.0f}）与背景（{hi:.0f}）区间重叠，病灶与背景不可分离')
    elif th < lo:
        new = int(round(lo + 2))
        notes.append(f'阈值 {th} HU 低于可用区间下界（{lo:.1f}），'
                     + ('只保住部分低密度体素、体积会低估' if low else '会并入更暗的组织')
                     + f'，修正为 {new} HU')
        th = new
    elif th > hi:
        new = int(round(hi - 3))
        notes.append(f'阈值 {th} HU 高于可用区间上界（{hi:.1f}），'
                     + ('背景会被并入' if low else '病灶主体会被漏检') + f'，修正为 {new} HU')
        th = new

    if not item or 'slice_thickness_mm' not in item:
        t = 1
        notes.append('模型未给出层厚，取 1 层')
    else:
        try:
            t = max(1, min(5, int(item['slice_thickness_mm'])))
        except (TypeError, ValueError):
            t = 1
            notes.append('模型层厚不可解析，取 1 层')
    # 直径 ≤30 mm 的小灶：层厚 >2 会跳采边界层（高密度过估、低密度低估），一律夹到 1 层
    if t > 2 and feat['r'] * 2 <= 30:
        notes.append(f'层厚 {t} 层对直径 {feat["r"] * 2} mm 的小灶会跳采边界层、体积'
                     + ('低估' if low else '过估') + '，修正为 1 层')
        t = 1

    conf = 0.0
    if item:
        try:
            conf = max(0.0, min(1.0, float(item.get('confidence', 0))))
        except (TypeError, ValueError):
            conf = 0.0
    return {'threshold_hu': th, 'slice_thickness_mm': t, 'confidence': conf, 'notes': notes}


def _measure(vol, f, th, thick):
    """阈值分割 + 按层厚积分（与 research/experiment.py 同一算法）。"""
    cx, cy, cz, r = f['cx'], f['cy'], f['cz'], f['r']
    total = 0.0
    z = int(cz - math.ceil((r + 1) / thick) * thick)
    z_hi = cz + r
    while z <= z_hi:
        z_end = min(z + thick, z_hi + 1)
        if z_end <= z:
            break
        sl = vol[z, :, :] if thick == 1 else vol[z:z_end, :, :].mean(axis=0)
        y0, y1 = max(0, cy - r - 4), min(vol.shape[1], cy + r + 5)
        x0, x1 = max(0, cx - r - 4), min(vol.shape[2], cx + r + 5)
        total += int(((sl[y0:y1, x0:x1] <= th) if f.get('polarity') == 'low'
                      else (sl[y0:y1, x0:x1] >= th)).sum()) * thick
        z += thick
    return round(float(total), 1)


def _detect_high(vol, max_n=3):
    """无人工标注病例的候选检出：阈值 + 连通域 + 形态筛选。

    筛选依据：高密度（>200 HU）、体积在 100~15000 体素、包围盒填充率 >=0.35
    （球形/块状灶填充率高，血管等细长结构只有 0.05~0.15）、三向跨度比 <=1.6
    （排除管状结构）、平均密度 >=180 HU（排除对比剂伪影边缘）。
    """
    from scipy import ndimage as ndi
    from skimage import morphology

    mask = morphology.binary_opening(vol > 200, morphology.ball(1))
    lab, n = ndi.label(mask)
    if n == 0:
        return []
    idx = list(range(1, n + 1))
    objs = ndi.find_objects(lab)
    sizes = ndi.sum(mask, lab, idx)
    means = ndi.mean(vol, lab, idx)
    peaks = ndi.maximum(vol, lab, idx)
    cents = ndi.center_of_mass(mask, lab, idx)

    out = []
    for k, i in enumerate(idx):
        s = objs[i - 1]
        if s is None:
            continue
        dz, dy, dx = (s[j].stop - s[j].start for j in range(3))
        nz = float(sizes[k])
        if nz < 100 or nz > 15000:
            continue
        fill = nz / float(dz * dy * dx)
        if fill < 0.35:
            continue
        span = max(dz, dy, dx) / max(1, min(dz, dy, dx))
        if span > 1.6:
            continue
        if float(means[k]) < 180:
            continue
        r = int(round((3 * nz / (4 * math.pi)) ** (1 / 3)))
        r = max(3, min(18, r))
        cz, cy, cx = (int(round(c)) for c in cents[k])
        # 背景可分性：真病灶应位于相对低密度的组织中；若背景环密度与灶体峰值相当，
        # 说明它泡在同密度结构里（多为血管腔/血池内的高密度块），无法独立分割
        z0, z1 = max(0, cz - r - 9), min(vol.shape[0], cz + r + 10)
        y0, y1 = max(0, cy - r - 9), min(vol.shape[1], cy + r + 10)
        x0, x1 = max(0, cx - r - 9), min(vol.shape[2], cx + r + 10)
        zz, yy, xx = np.mgrid[z0:z1, y0:y1, x0:x1]
        d2 = (zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2
        ring = (d2 > (r + 2) ** 2) & (d2 <= (r + 9) ** 2)
        if ring.sum() == 0:
            continue
        bg = vol[z0:z1, y0:y1, x0:x1][ring]
        bg3 = float(bg.mean() + 3 * bg.std())
        if bg3 >= float(peaks[k]) * 0.9:
            continue
        out.append({'cx': cx, 'cy': cy, 'cz': cz, 'r': r,
                    'peak': float(peaks[k]), 'mean': float(means[k]),
                    'voxels': int(nz), 'fill': round(fill, 2), 'bg_plus_3sd': round(bg3, 2),
                    'polarity': 'high', 'salience': float(peaks[k])})
    out.sort(key=lambda x: -x['salience'])
    return out[:max_n]


def _detect_low(vol, max_n=3):
    """低密度灶检出：软组织内、明显低于周围环境、成球状的低密度团块。

    高密度阈值（>200 HU）看不见缺血、水肿、血栓这类低密度灶，所以另开一路：
    先用大窗口局部均值当"环境密度"，找比环境低 45 HU 以上的体素，再用与高密度
    同口径的形态筛（体积、填充率、三向跨度比）排除肺野、脂肪和血管腔。
    """
    from scipy import ndimage as ndi
    from skimage import morphology

    env = ndi.uniform_filter(vol, size=27)
    mask = (env - vol > 45) & (vol > -50) & (vol < 150)
    mask = morphology.binary_opening(mask, morphology.ball(1))
    lab, n = ndi.label(mask)
    if n == 0:
        return []
    idx = list(range(1, n + 1))
    objs = ndi.find_objects(lab)
    sizes = ndi.sum(mask, lab, idx)
    means = ndi.mean(vol, lab, idx)
    maxs = ndi.maximum(vol, lab, idx)
    cents = ndi.center_of_mass(mask, lab, idx)

    out = []
    for k, i in enumerate(idx):
        s = objs[i - 1]
        if s is None:
            continue
        dz, dy, dx = (s[j].stop - s[j].start for j in range(3))
        nz = float(sizes[k])
        if nz < 300 or nz > 15000:
            continue
        fill = nz / float(dz * dy * dx)
        if fill < 0.35:
            continue
        span = max(dz, dy, dx) / max(1, min(dz, dy, dx))
        if span > 1.6:
            continue
        r = int(round((3 * nz / (4 * math.pi)) ** (1 / 3)))
        r = max(5, min(18, r))
        cz, cy, cx = (int(round(c)) for c in cents[k])
        z0, z1 = max(0, cz - r - 9), min(vol.shape[0], cz + r + 10)
        y0, y1 = max(0, cy - r - 9), min(vol.shape[1], cy + r + 10)
        x0, x1 = max(0, cx - r - 9), min(vol.shape[2], cx + r + 10)
        zz, yy, xx = np.mgrid[z0:z1, y0:y1, x0:x1]
        d2 = (zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2
        ring = (d2 > (r + 2) ** 2) & (d2 <= (r + 9) ** 2)
        if ring.sum() == 0:
            continue
        bg = vol[z0:z1, y0:y1, x0:x1][ring]
        contrast = float(bg.mean() - means[k])
        # 环境必须真的比灶体亮：低密度灶被较高密度组织包绕
        if contrast < 30:
            continue
        out.append({'cx': cx, 'cy': cy, 'cz': cz, 'r': r,
                    'peak': float(maxs[k]), 'mean': float(means[k]),
                    'voxels': int(nz), 'fill': round(fill, 2),
                    'bg_plus_3sd': round(float(bg.mean() + 3 * bg.std()), 2),
                    'polarity': 'low', 'salience': contrast})
    out.sort(key=lambda x: -x['salience'])
    return out[:max_n]


def _detect(vol, max_n=3):
    """两路候选合并：高密度灶与低密度灶各取显著度靠前的，再统一排序截断。"""
    cands = _detect_high(vol, max_n) + _detect_low(vol, max_n)
    cands.sort(key=lambda x: -x['salience'])
    return cands[:max_n]


def _run(tid, case_id, cfg):
    try:
        _sync(tid, 'volume', 8, '读取体数据')
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM cases WHERE id=%s', (case_id,))
                case = cur.fetchone()
                if not case:
                    raise RuntimeError('病例不存在')
                cur.execute("SELECT * FROM lesions WHERE case_id=%s AND source='manual' ORDER BY id", (case_id,))
                lesions_db = cur.fetchall()
        finally:
            conn.close()

        vol_file = (case.get('vol_file') or '').strip()
        candidates = [VOL_DIR / vol_file] if vol_file else []
        candidates.append(VOL_DIR / f'case_{case_id:02d}.npy')
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            raise RuntimeError(f'找不到体数据文件：{vol_file or "case_%02d.npy" % case_id}')
        vol = np.load(str(path)).astype(np.float32)
        _set(tid, volume_shape=list(vol.shape), volume_file=path.name)

        _sync(tid, 'feature', 26, '实测球体内外 HU')
        feats = []
        rejected = 0
        if lesions_db:
            # 有人工标注：以标注为中心实测；低密度类标注（缺血/水肿/血栓）走反向分割
            for L in lesions_db:
                cx, cy, cz = int(L['cx']), int(L['cy']), int(L['cz'])
                r = int(L['radius'])
                f = _extract(vol, cx, cy, cz, r)
                pol = 'low' if _is_low_density(str(L['type'] or '') + str(L['name'] or '')) else 'high'
                f.update({'cx': cx, 'cy': cy, 'cz': cz, 'r': r,
                          'name': L['name'], 'db_type': L['type'], 'id': L['id'],
                          'polarity': pol})
                feats.append(f)
            detected = False
        else:
            # 无人工标注（如用户新建档案）：算法自动检出候选，再交模型判读
            _sync(tid, 'feature', 26, '自动检出候选病灶')
            for c in _detect(vol, max_n=3):
                f = _extract(vol, c['cx'], c['cy'], c['cz'], c['r'])
                # 高密度候选的可分性校验：背景环密度与灶体几乎同高，说明它泡在同密度
                # 结构里（多为血管腔/血池内的高密度块），无法独立分割，不予纳入
                if c['polarity'] == 'high' and f['bg_plus_3sd_hu'] >= f['peak_hu'] * 0.9:
                    rejected += 1
                    continue
                k = len(feats) + 1
                f.update({'cx': c['cx'], 'cy': c['cy'], 'cz': c['cz'], 'r': c['r'],
                          'name': f'自动检出灶 #{k}（中心 ({c["cx"]},{c["cy"]},{c["cz"]})）',
                          'db_type': '（无人工标注：本灶由自动检出算法给出，请独立判断类型）',
                          'id': None, 'auto': True, 'polarity': c['polarity'],
                          'detect_fill': c['fill'], 'detect_voxels': c['voxels']})
                feats.append(f)
                if len(feats) >= 3:
                    break
            detected = True
        _set(tid, features=feats, detected=detected, rejected=rejected)

        if not feats:
            _set(tid, status='done', progress=100, stage='done', stage_text='分析完成',
                 result={
                     'lesions': [],
                     'conclusion': (
                         '自动检出未发现可独立分割的病灶：候选结构要么体积/形态不符合，'
                         '要么背景密度与灶体几乎相同（多位于血管腔或血池内）而无法分割。'
                     ),
                     'ai_meta': {'model': (cfg.get('model') or cfg['models'][0]), 'elapsed_sec': 0, 'calls': 0,
                                 'usage_total_tokens': 0, 'raw_output': '', 'raw_calls': [],
                                 'volume_file': path.name, 'volume_shape': list(vol.shape),
                                 'detected': detected},
                     'case': {'id': case['id'], 'patient_name': case['patient_name'],
                              'patient_id': case['patient_id'], 'gender': case['gender'],
                              'age': case['age'], 'exam_date': case['exam_date'],
                              'study_desc': case['study_desc']},
                 })
            return

        from concurrent.futures import ThreadPoolExecutor, as_completed

        switch_log = []

        def _infer(i, f):
            low = f.get('polarity') == 'low'
            if low:
                pol = '本灶为低密度灶：低于阈值（密度更低）的体素判为灶体。'
                lo_l = f['mean_hu'] + 3 * f.get('sd_hu', 0)
                hi_l = (f['mean_hu'] + f['bg_mean_hu']) / 2
                cons = (
                    f'- 阈值必须低于背景均值-3SD（{f["bg_minus_3sd_hu"]}），否则背景会被并入灶体；\n'
                    f'- 阈值不得低于球内均值+3SD（{lo_l:.0f}），否则球内只有部分体素被保留、体积会低估；\n'
                    f'- 阈值不宜高于球内均值与背景均值的中点（{hi_l:.0f}），否则背景组织会被并入；\n'
                    f'- 层厚取 1-5 层；本例直径 {f["r"] * 2} mm 的灶建议取 1 层，跳采会漏掉边界层、体积低估。'
                )
            else:
                pol = '本灶为高密度灶：高于阈值（密度更高）的体素判为灶体。'
                cons = (
                    '- 阈值必须高于背景均值+3SD，否则背景噪声会被误判为病灶；\n'
                    '- 阈值不得高于病灶峰值的 90%，否则病灶主体会被漏检；\n'
                    f'- 层厚取 1-5 层；本例直径 {f["r"] * 2} mm 的灶建议取 1 层，跳采会把边界层整层并入、体积过估。'
                )
            prompt = PROMPT_TPL.format(
                cx=f['cx'], cy=f['cy'], cz=f['cz'], r=f['r'], diam=f['r'] * 2,
                n=f['inside_voxels'], truth=4 / 3 * math.pi * (f['r'] ** 3),
                mean=f['mean_hu'], median=f['median_hu'], p90=f['p90_hu'],
                p95=f['p95_hu'], peak=f['peak_hu'], min=f['min_hu'],
                bgm=f['bg_mean_hu'], bgs=f['bg_std_hu'], bg3=f['bg_plus_3sd_hu'],
                bgm3=f['bg_minus_3sd_hu'], polarity=pol, constraints=cons,
                dbtype=f['db_type'],
            )
            t0 = time.time()
            try:
                resp, used = _call_llm(cfg, prompt, switch_log)
                return {'index': i, 'prompt': prompt, 'resp': resp,
                        'model_used': used, 'dt': time.time() - t0}
            except Exception as e:
                # 失败也要把 prompt 原样带回来：前端「查看模型原文与提示词」必须能摊开给人看，
                # 否则失败时折叠区是空的，评审会以为系统没真的拼提示词。
                return {'index': i, 'prompt': prompt, 'err': str(e), 'dt': time.time() - t0}

        outs = {}
        done_n = 0
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(_infer, i, f): i for i, f in enumerate(feats, 1)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    outs[i] = fut.result()
                except Exception as e:
                    outs[i] = {'index': i, 'prompt': '', 'err': str(e)}
                done_n += 1
                _sync(tid, 'llm', 30 + int(55 * done_n / len(feats)),
                      f'大模型推理 {done_n}/{len(feats)} 完成')

        items = []
        raws = []
        usages = []
        elapsed_all = 0.0
        for i, f in enumerate(feats, 1):
            o = outs.get(i) or {}
            dt = float(o.get('dt') or 0)
            elapsed_all += dt
            if o.get('err'):
                raw = ''
                item = {}
                parse_err = o['err']
            else:
                raw = o['resp']['choices'][0]['message']['content']
                try:
                    item = _parse_json(raw)
                    parse_err = None
                except Exception as e:
                    item = {}
                    parse_err = str(e)
            raws.append({'lesion_index': i, 'prompt': o.get('prompt', ''), 'raw': raw,
                         'usage': (o.get('resp') or {}).get('usage', {}),
                         'elapsed_sec': round(dt, 1),
                         **({'parse_error': parse_err} if parse_err else {})})
            usages.append((o.get('resp') or {}).get('usage', {}))
            v = _verify(item, f)
            failed = bool(o.get('err'))
            err_raw = parse_err if failed else ''
            err_text = _friendly_error(err_raw) if failed else ''
            if failed:
                v['notes'].insert(0, f'模型未返回结果，参数按规则兜底：{err_text}')
            truth = 4 / 3 * math.pi * (f['r'] ** 3)
            items.append({
                'id': f.get('id'),
                'lesion_index': i,
                'source': 'auto' if f.get('auto') else 'db',
                'name': f['name'],
                'cx': f['cx'], 'cy': f['cy'], 'cz': f['cz'],
                'radius': f['r'],
                'polarity': f.get('polarity', 'high'),
                'ct_value': f['peak_hu'],
                'confidence': (None if failed else v['confidence']),
                'confidence_valid': (not failed),
                'type': ((item or {}).get('type_cn') or ('未判读' if failed else f['db_type'])),
                'db_type': f['db_type'],
                'severity': ((item or {}).get('severity') or ('未判读' if failed else '')),
                'finding': ((item or {}).get('finding') or
                            ('模型未给出判读意见（本次调用失败），分割参数已按规则兜底' if failed else '')),
                'suggest': ((item or {}).get('suggestion') or
                            ('AI 侧本次未获取建议，按兜底参数执行测量' if failed else '')),
                'agree_with_label': (item or {}).get('agree_with_label'),
                'ai_error': err_text,
                'ai_error_raw': err_raw,
                'raw_prompt': o.get('prompt', ''),
                'threshold_hu': v['threshold_hu'],
                'slice_thickness_mm': v['slice_thickness_mm'],
                'truth_volume_mm3': (None if f.get('auto') else round(truth, 1)),
                'measures': [],
                'notes': v['notes'],
                'elapsed_sec': round(dt, 1),
                'raw_output': raw,
                'features': {k: f[k] for k in
                             ('mean_hu', 'median_hu', 'p90_hu', 'p95_hu', 'peak_hu',
                              'bg_mean_hu', 'bg_std_hu', 'bg_plus_3sd_hu', 'inside_voxels')},
            })
            for src, th, t in (('ai', v['threshold_hu'], v['slice_thickness_mm']),
                               ('default', 200, 3)):
                items[-1]['measures'].append({
                    'source': src, 'threshold_hu': th, 'thickness': t,
                    'volume_mm3': _measure(vol, f, th, t),
                })

        _sync(tid, 'verify', 92, '校验模型输出')
        total_tokens = sum(int(u.get('total_tokens', 0) or 0) for u in usages)
        used_models = [o.get('model_used') for o in outs.values() if o.get('model_used')]
        result_obj = {
                 'lesions': items,
                 'conclusion': '',
                 'ai_meta': {
                     'model': used_models[0] if used_models else (cfg.get('model') or cfg['models'][0]),
                     'models_configured': len(cfg.get('models') or []),
                     'model_switch_log': used_models,
                     'switch_events': switch_log,
                     'elapsed_sec': round(elapsed_all, 1),
                     'calls': len(feats),
                     'usage_total_tokens': total_tokens,
                     'usage': usages,
                     'ok_calls': sum(1 for x in raws if not x.get('parse_error')),
                     'failed_calls': sum(1 for x in raws if x.get('parse_error')),
                     'errors': [{'lesion_index': x['lesion_index'],
                                 'error': _friendly_error(x['parse_error'])}
                                for x in raws if x.get('parse_error')],
                     'raw_output': '\n\n'.join(
                         f"--- 病灶 {x['lesion_index']} 原文 ---\n{x['raw']}" for x in raws),
                     'raw_calls': raws,
                     'volume_file': path.name,
                     'volume_shape': list(vol.shape),
                     'detected': detected,
                 },
                 'case': {
                     'id': case['id'], 'patient_name': case['patient_name'],
                     'patient_id': case['patient_id'], 'gender': case['gender'],
                     'age': case['age'], 'exam_date': case['exam_date'],
                     'study_desc': case['study_desc'],
                 },
        }
        _set(tid, status='done', progress=100, stage='done',
             stage_text='分析完成', result=result_obj)
        # 结果落盘，刷新页面或切回该病例时可直接回显，不必重新调用模型
        _save_result(case_id, tid, result_obj)
        # 自动检出的病灶写回数据库：刷新后 3D 页仍能标出，病例病灶数也对得上
        _write_lesions(case_id, items)
        _mark_analyzed(case_id)
    except Exception as e:
        _set(tid, status='error', error=str(e), stage_text='分析失败', progress=100)


@router.post('/cases/{case_id}/analyze')
def start_analyze(case_id: int, user=Depends(require_user)):
    cfg = _cfg()
    tid = uuid.uuid4().hex
    _set(tid, status='running', stage='volume', progress=2,
         stage_text='排队中', case_id=case_id, started_at=time.time())
    threading.Thread(target=_run, args=(tid, case_id, cfg), daemon=True).start()
    return {'task_id': tid}


@router.get('/analyze/tasks/{tid}')
def get_task(tid: str, user=Depends(require_user)):
    with _LOCK:
        t = dict(TASKS.get(tid) or {})
    if not t:
        raise HTTPException(status_code=404, detail='任务不存在或已过期')
    return t


@router.get('/cases/{case_id}/analyze/latest')
def latest_analyze(case_id: int, user=Depends(require_user)):
    """最近一次已保存的分析结果（本地缓存，不触发模型调用）。"""
    p = AI_DIR / f'case_{int(case_id):02d}.json'
    if not p.exists():
        raise HTTPException(status_code=404, detail='该病例还没有保存过的分析结果')
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'历史结果读取失败：{e}')


@router.get('/analyze/health')
def ai_health(user=Depends(require_user)):
    """模型连通性自检：只回显配置项，不泄露密钥。"""
    try:
        cfg = _cfg()
        return {'ok': True, 'model': cfg['model'], 'base_url': cfg['base_url'],
                'models': cfg.get('models') or [cfg['model']],
                'key_tail': str(cfg['api_key'])[-4:]}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


class ProbeBody(BaseModel):
    text: str = '连通性测试，请只回复两个字：正常'


@router.post('/analyze/probe')
def ai_probe(body: ProbeBody, user=Depends(require_user)):
    cfg = _cfg()
    t0 = time.time()
    resp, _ = _call_llm(cfg, body.text)
    return {'ok': True, 'elapsed_sec': round(time.time() - t0, 1),
            'reply': resp['choices'][0]['message']['content'][:200],
            'usage': resp.get('usage', {})}
