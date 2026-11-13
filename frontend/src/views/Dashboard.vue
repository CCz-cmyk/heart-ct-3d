<template>
  <div class="dashboard">
    <header class="topbar">
      <div class="brand">
        <span class="logo" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="15" height="15">
            <rect x="10" y="5" width="4" height="14" rx="1" />
            <rect x="5" y="10" width="14" height="4" rx="1" />
          </svg>
        </span>
        <div class="brand-text">
          <div class="brand-name">心脏CT 3D可视化医疗辅助系统</div>
          <div class="brand-sub">Heart CT 3D Visualization &amp; AI Assistant</div>
        </div>
      </div>
      <svg class="ecg" viewBox="0 0 400 24" preserveAspectRatio="none" aria-hidden="true">
        <path class="ecg-base" d="M0 18 L36 18 L44 18 L50 5 L56 23 L62 11 L68 18 L118 18 L126 18 L132 4 L138 24 L144 9 L150 18 L232 18 L240 18 L246 5 L252 23 L258 11 L264 18 L400 18" />
        <path class="ecg-line" d="M0 18 L36 18 L44 18 L50 5 L56 23 L62 11 L68 18 L118 18 L126 18 L132 4 L138 24 L144 9 L150 18 L232 18 L240 18 L246 5 L252 23 L258 11 L264 18 L400 18" />
      </svg>
      <div class="user-box">
        <span class="avatar">{{ (user.real_name || user.username || 'U').slice(0, 1) }}</span>
        <span class="user-name">{{ user.real_name || user.username }}</span>
        <span class="user-role">{{ user.role === 'admin' ? '管理员' : '医生' }}</span>
        <button @click="logout">退出登录</button>
      </div>
    </header>

    <div class="content">
      <div class="stat-row">
        <div class="stat c-blue"><div class="stat-lbl">病例总数<em>CASES</em></div><div class="num">{{ cases.length }}</div></div>
        <div class="stat c-green"><div class="stat-lbl">已分析<em>ANALYZED</em></div><div class="num">{{ analyzedCount }}</div></div>
        <div class="stat c-amber"><div class="stat-lbl">检出病灶<em>LESIONS</em></div><div class="num">{{ lesionTotal }}</div></div>
      </div>

      <div class="toolbar">
        <div class="search-wrap">
          <svg class="search-ico" viewBox="0 0 24 24" width="14" height="14">
            <circle cx="11" cy="11" r="6.5" />
            <line x1="16.5" y1="16.5" x2="21" y2="21" />
          </svg>
          <input v-model="keyword" class="search" placeholder="按姓名 / 检查号检索" />
        </div>
        <button class="create-btn" @click="openCreate"><span class="plus">+</span>新建档案</button>
      </div>

      <div v-if="notice" class="notice">{{ notice }}</div>

      <div v-if="showDlg" class="mask" @click.self="closeDlg">
        <div class="dialog">
          <div class="dlg-title">{{ dlgMode === 'create' ? '新建档案 · 上传 DICOM 序列' : '编辑档案 · 患者信息' }}</div>
          <div class="grid">
            <label>检查号<input v-model="form.patient_id" placeholder="如 P0011" /></label>
            <label>姓名<input v-model="form.patient_name" placeholder="患者姓名" /></label>
            <label>性别
              <select v-model="form.gender">
                <option value="男">男</option>
                <option value="女">女</option>
              </select>
            </label>
            <label>年龄<input v-model.number="form.age" type="number" min="0" max="130" /></label>
            <label>检查日期<input v-model="form.exam_date" type="date" /></label>
            <label>检查项目<input v-model="form.study_desc" placeholder="如 冠状动脉CTA平扫" /></label>
          </div>
          <div v-if="dlgMode === 'create'" class="pick">
            <input ref="fileInput" type="file" accept=".zip" @change="onPick" />
            <div class="tip">上传包含逐层 .dcm 的 .zip（≤500 MB、≤1200 片）；<br />系统会解包、解析并生成体数据，创建后可直接三维查看</div>
          </div>
          <div v-else class="pick">
            <div class="tip">仅修改患者信息，影像数据（DICOM 序列与体数据）保持不变。</div>
          </div>
          <div v-if="errMsg" class="err">{{ errMsg }}</div>
          <div class="dlg-actions">
            <button class="ghost" :disabled="busy" @click="closeDlg">取消</button>
            <button class="primary" :disabled="busy" @click="submitForm">
              {{ busy ? '处理中...' : (dlgMode === 'create' ? '创建并解析' : '保存修改') }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="pendingDelete" class="mask" @click.self="cancelDelete">
        <div class="dialog small">
          <div class="dlg-title">删除档案</div>
          <div class="del-text">
            确定删除 <b>{{ pendingDelete.patient_id }} {{ pendingDelete.patient_name }}</b> 吗？<br />
            病灶、体数据与 DICOM 序列文件会一并删除，<b>不可恢复</b>。
          </div>
          <div v-if="errMsg" class="err">{{ errMsg }}</div>
          <div class="dlg-actions">
            <button class="ghost" :disabled="busy" @click="cancelDelete">取消</button>
            <button class="danger" :disabled="busy" @click="doDelete">{{ busy ? '删除中...' : '确认删除' }}</button>
          </div>
        </div>
      </div>

      <div class="table-card">
        <i class="tcorner tl" aria-hidden="true"></i>
        <i class="tcorner tr" aria-hidden="true"></i>
        <i class="tcorner bl" aria-hidden="true"></i>
        <i class="tcorner br" aria-hidden="true"></i>
        <table class="case-table">
          <thead>
            <tr>
              <th><span>检查号</span><em>ID</em></th>
              <th><span>姓名</span><em>NAME</em></th>
              <th><span>性别</span><em>SEX</em></th>
              <th><span>年龄</span><em>AGE</em></th>
              <th><span>检查日期</span><em>DATE</em></th>
              <th><span>检查项目</span><em>PROTOCOL</em></th>
              <th><span>病灶数</span><em>LESIONS</em></th>
              <th><span>状态</span><em>STATUS</em></th>
              <th><span>操作</span><em>ACTION</em></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!filtered.length">
              <td colspan="9" class="empty-row">没有匹配的档案</td>
            </tr>
            <tr v-for="c in filtered" :key="c.id">
              <td class="mono">{{ c.patient_id }}</td>
              <td class="pname">{{ c.patient_name }}</td>
              <td>{{ c.gender }}</td>
              <td class="num-col">{{ c.age }}</td>
              <td class="mono dim">{{ c.exam_date }}</td>
              <td class="desc">{{ c.study_desc }}</td>
              <td class="num-col">{{ c.lesion_count }}<span v-if="c.lesion_source === 'auto'" class="auto-tag" title="无人工标注，由 AI 自动检出">自动</span></td>
              <td>
                <span class="status" :class="c.status === '已分析' ? 'done' : 'todo'">
                  <i class="dot"></i>{{ c.status }}
                </span>
              </td>
              <td class="ops">
                <button class="view-btn" @click="$router.push(`/viewer/${c.id}`)">3D 查看</button>
                <button class="edit-btn" @click="openEdit(c)">编辑</button>
                <button class="del-btn" @click="askDelete(c)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const cases = ref([])
const keyword = ref('')
const user = ref(JSON.parse(localStorage.getItem('user') || '{}'))

const showDlg = ref(false)
const dlgMode = ref('create')
const editingId = ref(null)
const busy = ref(false)
const errMsg = ref('')
const notice = ref('')
const pickedFile = ref(null)
const pendingDelete = ref(null)
const form = ref({ patient_id: '', patient_name: '', gender: '男', age: 50, exam_date: '', study_desc: '' })

function flash(msg) {
  notice.value = msg
  setTimeout(() => { notice.value = '' }, 5000)
}

function openCreate() {
  const d = new Date()
  form.value = {
    patient_id: '', patient_name: '', gender: '男', age: 50,
    exam_date: d.toISOString().slice(0, 10), study_desc: '',
  }
  dlgMode.value = 'create'
  editingId.value = null
  pickedFile.value = null
  errMsg.value = ''
  showDlg.value = true
}

function openEdit(c) {
  form.value = {
    patient_id: c.patient_id, patient_name: c.patient_name, gender: c.gender || '男',
    age: c.age || 0, exam_date: c.exam_date || '', study_desc: c.study_desc || '',
  }
  dlgMode.value = 'edit'
  editingId.value = c.id
  pickedFile.value = null
  errMsg.value = ''
  showDlg.value = true
}

function closeDlg() {
  if (busy.value) return
  showDlg.value = false
}

function onPick(e) {
  pickedFile.value = (e.target.files && e.target.files[0]) || null
}

async function submitForm() {
  errMsg.value = ''
  const f = form.value
  if (!String(f.patient_id).trim() || !String(f.patient_name).trim()) {
    errMsg.value = '检查号与姓名不能为空'
    return
  }
  if (dlgMode.value === 'create' && !pickedFile.value) {
    errMsg.value = '请选择 DICOM 序列的 .zip 文件'
    return
  }
  busy.value = true
  try {
    if (dlgMode.value === 'create') {
      const fd = new FormData()
      fd.append('patient_id', String(f.patient_id).trim())
      fd.append('patient_name', String(f.patient_name).trim())
      fd.append('gender', f.gender)
      fd.append('age', Number(f.age) || 0)
      fd.append('exam_date', f.exam_date || '')
      fd.append('study_desc', f.study_desc || '')
      fd.append('file', pickedFile.value)
      const created = await api.createCase(fd)
      flash(`已创建档案 ${created.patient_id} ${created.patient_name}，体数据 ${created.vol_dims.split(',').join('×')}，共 ${created.slices} 层`)
    } else {
      const updated = await api.updateCase(editingId.value, {
        patient_id: String(f.patient_id).trim(),
        patient_name: String(f.patient_name).trim(),
        gender: f.gender,
        age: Number(f.age) || 0,
        exam_date: f.exam_date || '',
        study_desc: f.study_desc || '',
      })
      flash(`已更新档案 ${updated.patient_id} ${updated.patient_name}`)
    }
    cases.value = await api.listCases()
    showDlg.value = false
  } catch (e) {
    errMsg.value = e.message
  } finally {
    busy.value = false
  }
}

function askDelete(c) {
  pendingDelete.value = c
  errMsg.value = ''
}

function cancelDelete() {
  if (busy.value) return
  pendingDelete.value = null
}

async function doDelete() {
  const c = pendingDelete.value
  if (!c) return
  busy.value = true
  errMsg.value = ''
  try {
    await api.deleteCase(c.id)
    cases.value = await api.listCases()
    pendingDelete.value = null
    flash(`已删除档案 ${c.patient_id} ${c.patient_name}`)
  } catch (e) {
    errMsg.value = e.message
  } finally {
    busy.value = false
  }
}

const filtered = computed(() => {
  const k = keyword.value.trim()
  if (!k) return cases.value
  return cases.value.filter((c) => c.patient_name.includes(k) || c.patient_id.includes(k))
})
const analyzedCount = computed(() => cases.value.filter((c) => c.status === '已分析').length)
const lesionTotal = computed(() => cases.value.reduce((s, c) => s + (c.lesion_count || 0), 0))

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  router.push('/login')
}

onMounted(async () => {
  try {
    cases.value = await api.listCases()
  } catch (e) {
    alert(e.message)
  }
})
</script>

<style scoped>
.dashboard {
  height: 100%;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
  color-scheme: dark;
  color: #dceefc;
  font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', system-ui, sans-serif;
  background:
    radial-gradient(circle at 16% -10%, #12456b 0%, transparent 44%),
    radial-gradient(circle at 92% 2%, rgba(96, 74, 210, .22) 0%, transparent 38%),
    linear-gradient(180deg, #06101d 0%, #040a13 60%, #03070c 100%);
}
.dashboard::before {
  content: '';
  position: absolute; inset: 0; pointer-events: none;
  background-image: radial-gradient(rgba(120, 200, 255, .1) 1px, transparent 1px);
  background-size: 15px 15px;
  mask-image: radial-gradient(circle at 50% 0%, #000 0%, rgba(0, 0, 0, .4) 42%, transparent 74%);
  -webkit-mask-image: radial-gradient(circle at 50% 0%, #000 0%, rgba(0, 0, 0, .4) 42%, transparent 74%);
}

/* ---------- 顶栏 ---------- */
.topbar {
  position: relative; z-index: 2;
  display: flex; align-items: center; gap: 16px;
  padding: 11px 22px;
  background: linear-gradient(180deg, rgba(10, 24, 40, .94), rgba(6, 14, 24, .88));
  backdrop-filter: blur(14px);
  border-bottom: 1px solid rgba(60, 170, 240, .13);
  box-shadow: 0 12px 34px rgba(2, 7, 13, .6);
}
.topbar::after {
  content: '';
  position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(41, 224, 255, .5) 22%, rgba(139, 124, 246, .4) 68%, transparent);
}
.brand { display: flex; align-items: center; gap: 11px; flex: none; }
.logo {
  width: 30px; height: 30px; flex: none;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(150deg, #22d7ff, #7b5cf0);
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
  box-shadow: 0 0 20px rgba(41, 224, 255, .34);
}
.logo svg { fill: #04121f; }
.brand-name {
  font-size: 14px; font-weight: 700; letter-spacing: .03em; line-height: 1.2;
  background: linear-gradient(100deg, #f2fbff 20%, #9fe6ff 78%, #bdb3ff 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.brand-sub {
  margin-top: 2px;
  font-family: 'Bahnschrift', 'DIN Alternate', 'Segoe UI', sans-serif;
  font-size: 8px; letter-spacing: .24em; color: #4b7796;
}

.ecg { flex: 1; min-width: 0; height: 22px; margin: 0 10px; opacity: .9; pointer-events: none; }
.ecg-base { fill: none; stroke: rgba(41, 224, 255, .3); stroke-width: 1.1; }
.ecg-line {
  fill: none; stroke: rgba(41, 224, 255, .8); stroke-width: 1.2;
  stroke-dasharray: 34 400;
  animation: ecgFlow 6s linear infinite;
  filter: drop-shadow(0 0 3px rgba(41, 224, 255, .7));
}
@keyframes ecgFlow { to { stroke-dashoffset: -434; } }

.user-box {
  display: flex; align-items: center; gap: 9px; flex: none;
  padding: 3px 4px 3px 3px;
  border-radius: 999px;
  background: rgba(11, 26, 42, .6);
  border: 1px solid rgba(90, 180, 240, .15);
}
.avatar {
  width: 24px; height: 24px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; color: #04121e;
  background: linear-gradient(150deg, #33e0ff, #8b7cf6);
  box-shadow: 0 0 14px rgba(41, 224, 255, .35);
}
.user-name { font-size: 12px; color: #d3e6f8; }
.user-role {
  padding: 1px 8px; border-radius: 999px;
  font-size: 9.5px; letter-spacing: .06em;
  color: #c3b4ff; background: rgba(139, 124, 246, .16);
  box-shadow: inset 0 0 0 1px rgba(139, 124, 246, .28);
}
.user-box button {
  margin-right: 2px;
  padding: 4px 11px; border-radius: 999px; cursor: pointer;
  border: 1px solid rgba(90, 180, 240, .22);
  background: transparent; color: #9db8d0; font-size: 11.5px;
  transition: .16s;
}
.user-box button:hover {
  color: #e6f6ff; border-color: rgba(41, 224, 255, .55);
  background: rgba(41, 224, 255, .08);
}

/* ---------- 内容区 ---------- */
.content {
  position: relative; z-index: 1;
  flex: 1; overflow-y: auto;
  padding: 20px 26px 32px 22px;
}
.content::-webkit-scrollbar { width: 8px; }
.content::-webkit-scrollbar-track { background: transparent; }
.content::-webkit-scrollbar-thumb { background: #1a3348; border-radius: 4px; }
.content::-webkit-scrollbar-thumb:hover { background: #24506e; }

/* ---------- 统计卡 ---------- */
.stat-row { display: flex; gap: 14px; margin-bottom: 16px; }
.stat {
  position: relative; flex: 1; height: 74px;
  background:
    repeating-linear-gradient(135deg, rgba(90, 180, 240, .085) 0 1px, transparent 1px 8px),
    linear-gradient(150deg, #0f2740 0%, #0a1729 62%, #081220 100%);
  clip-path: polygon(0 0, calc(100% - 18px) 0, 100% 18px, 100% 100%, 0 100%);
  box-shadow: inset 0 1px 0 rgba(150, 220, 255, .1);
  transition: filter .2s, transform .2s;
}
.stat:hover { filter: brightness(1.1); transform: translateY(-2px); }
.stat::before {
  content: '';
  position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
}
.stat.c-blue::before { background: linear-gradient(180deg, #29e0ff, rgba(41, 224, 255, 0)); box-shadow: 0 0 12px rgba(41, 224, 255, .7); }
.stat.c-green::before { background: linear-gradient(180deg, #2ee6a8, rgba(46, 230, 168, 0)); box-shadow: 0 0 12px rgba(46, 230, 168, .6); }
.stat.c-amber::before { background: linear-gradient(180deg, #ffc46b, rgba(255, 196, 107, 0)); box-shadow: 0 0 12px rgba(255, 196, 107, .55); }
.stat::after {
  content: '';
  position: absolute; right: 0; top: 0;
  width: 18px; height: 18px;
  background: linear-gradient(135deg, transparent 48%, rgba(120, 210, 255, .22) 49%, transparent 62%);
}

.stat-lbl {
  position: absolute; left: 15px; top: 12px;
  font-size: 10.5px; letter-spacing: .14em; color: #7d9dba;
}
.stat-lbl em {
  display: block; margin-top: 3px;
  font-family: 'Bahnschrift', 'Consolas', monospace;
  font-size: 7.5px; letter-spacing: .2em; font-style: normal;
  color: #41647f;
}
.num {
  position: absolute; right: 22px; bottom: 10px;
  font-family: 'Bahnschrift', 'DIN Alternate', 'Consolas', sans-serif;
  font-size: 31px; font-weight: 700; line-height: 1; letter-spacing: -.5px;
}
.stat.c-blue .num { color: #8ce6ff; text-shadow: 0 0 20px rgba(41, 224, 255, .55); }
.stat.c-green .num { color: #63f2bd; text-shadow: 0 0 20px rgba(46, 230, 168, .45); }
.stat.c-amber .num { color: #ffcf85; text-shadow: 0 0 20px rgba(255, 196, 107, .45); }

/* ---------- 工具条 ---------- */
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.search-wrap { position: relative; display: flex; align-items: center; }
.search-ico {
  position: absolute; left: 14px; stroke: #5f97bd; fill: none;
  stroke-width: 1.7; pointer-events: none;
}
.search {
  width: 306px; height: 36px; padding: 0 16px 0 37px;
  border-radius: 999px;
  border: 1px solid rgba(90, 180, 240, .18);
  background: rgba(6, 17, 29, .9);
  color: #dff0ff; font-size: 12.5px; letter-spacing: .02em;
  outline: none; transition: .18s;
}
.search::placeholder { color: #4d7590; }
.search:focus {
  border-color: rgba(41, 224, 255, .6);
  box-shadow: 0 0 0 3px rgba(41, 224, 255, .1), inset 0 0 22px rgba(41, 224, 255, .07);
}
.create-btn {
  height: 36px; padding: 0 18px; border: none; cursor: pointer;
  display: flex; align-items: center; gap: 7px;
  clip-path: polygon(12px 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%, 0 12px);
  background: linear-gradient(100deg, #16c8e8, #5f8ef7 58%, #7b5cf0);
  color: #04121f; font-size: 12.5px; font-weight: 700; letter-spacing: .04em;
  transition: filter .16s, box-shadow .16s;
}
.create-btn:hover { filter: brightness(1.12); box-shadow: 0 0 26px rgba(41, 224, 255, .35); }
.create-btn .plus { font-size: 15px; line-height: 1; font-weight: 400; }

.notice {
  margin-bottom: 12px; padding: 10px 14px;
  border-radius: 3px;
  background: rgba(46, 230, 168, .09);
  border: 1px solid rgba(46, 230, 168, .28);
  border-left: 2px solid #2ee6a8;
  color: #86f2c8; font-size: 12.5px;
}

/* ---------- 表格 ---------- */
.table-card {
  position: relative;
  background: linear-gradient(180deg, rgba(10, 25, 40, .93), rgba(7, 16, 27, .96));
  border: 1px solid rgba(80, 175, 240, .13);
  border-radius: 4px; overflow: hidden;
  box-shadow: 0 22px 54px rgba(2, 7, 13, .58), inset 0 1px 0 rgba(140, 215, 255, .06);
}
.table-card::before {
  content: '';
  position: absolute; left: 0; right: 0; top: 0; height: 1px;
  background: linear-gradient(90deg, rgba(41, 224, 255, .55), rgba(139, 124, 246, .3), transparent 78%);
}
.tcorner { position: absolute; width: 14px; height: 14px; z-index: 2; pointer-events: none; }
.tcorner.tl { left: -1px; top: -1px; border-left: 1px solid rgba(41, 224, 255, .6); border-top: 1px solid rgba(41, 224, 255, .6); }
.tcorner.tr { right: -1px; top: -1px; border-right: 1px solid rgba(139, 124, 246, .45); border-top: 1px solid rgba(139, 124, 246, .45); }
.tcorner.bl { left: -1px; bottom: -1px; border-left: 1px solid rgba(139, 124, 246, .3); border-bottom: 1px solid rgba(139, 124, 246, .3); }
.tcorner.br { right: -1px; bottom: -1px; border-right: 1px solid rgba(41, 224, 255, .35); border-bottom: 1px solid rgba(41, 224, 255, .35); }

.case-table { width: 100%; border-collapse: collapse; }
.case-table th {
  padding: 8px 14px 7px;
  background: rgba(6, 16, 28, .55);
  border-bottom: 1px solid rgba(70, 160, 230, .17);
  text-align: left; white-space: nowrap; vertical-align: middle;
}
.case-table th span {
  display: block;
  font-size: 10.5px; font-weight: 600; letter-spacing: .1em; color: #8aa9c4;
}
.case-table th em {
  display: block; margin-top: 2px;
  font-family: 'Bahnschrift', 'Consolas', monospace;
  font-size: 7.5px; letter-spacing: .19em; font-style: normal; font-weight: 400;
  color: #5d87a6;
}
.case-table td {
  padding: 12px 14px;
  font-size: 12.5px; color: #c6dcf0;
  border-bottom: 1px solid rgba(90, 170, 230, .055);
}
.case-table tbody tr:last-child td { border-bottom: none; }
.case-table tbody tr { transition: background .14s; }
.case-table tbody tr:hover td { background: rgba(41, 224, 255, .05); }
.case-table tbody tr:hover td:first-child { box-shadow: inset 3px 0 0 #29e0ff; }
.case-table th:nth-child(4), .case-table td:nth-child(4),
.case-table th:nth-child(7), .case-table td:nth-child(7) { text-align: right; }
.case-table th:last-child, .case-table td:last-child { text-align: right; }
.case-table th:nth-child(4) em, .case-table th:nth-child(7) em, .case-table th:last-child em { text-align: right; }

.mono { font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; color: #9fe0ff; letter-spacing: .02em; }
.dim { color: #7e9cb8; }
.pname { font-weight: 600; color: #eaf6ff; }
.desc { color: #9db6cd; }
.num-col {
  font-family: 'Bahnschrift', 'Consolas', sans-serif;
  font-size: 14px; font-weight: 600; color: #eaf6ff;
}
.empty-row { text-align: center !important; padding: 38px 0 !important; color: #557894; font-size: 12.5px; }

.status {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px 3px 8px; border-radius: 999px;
  font-size: 10.5px; letter-spacing: .04em; white-space: nowrap;
}
.status .dot { width: 5px; height: 5px; border-radius: 50%; }
.status.done {
  background: rgba(46, 230, 168, .1); color: #5ff0c0;
  box-shadow: inset 0 0 0 1px rgba(46, 230, 168, .3);
}
.status.done .dot { background: #2ee6a8; animation: dotPulse 2.2s ease-in-out infinite; }
.status.todo {
  background: rgba(255, 196, 107, .1); color: #ffcf85;
  box-shadow: inset 0 0 0 1px rgba(255, 196, 107, .28);
}
.status.todo .dot { background: #ffc46b; animation: dotPulse 2.2s ease-in-out infinite; }
@keyframes dotPulse { 0%, 100% { opacity: .45; box-shadow: 0 0 0 0 currentColor; } 50% { opacity: 1; } }

.auto-tag {
  margin-left: 8px; padding: 1px 5px;
  border-radius: 2px;
  background: rgba(41, 224, 255, .12); color: #86e4ff;
  font-size: 9px; letter-spacing: .04em; vertical-align: 1px;
  box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .2);
}

.ops { white-space: nowrap; }
.ops button {
  height: 27px; padding: 0 11px; cursor: pointer;
  font-size: 11.5px; margin-left: 6px; transition: .15s;
  border-radius: 3px;
}
.view-btn {
  border: none;
  clip-path: polygon(7px 0, 100% 0, 100% calc(100% - 7px), calc(100% - 7px) 100%, 0 100%, 0 7px);
  background: linear-gradient(100deg, #12a9cc, #4478e2);
  color: #f2feff; font-weight: 700; letter-spacing: .04em;
  transition: filter .15s, box-shadow .15s;
}
.view-btn:hover { filter: brightness(1.2); box-shadow: 0 0 20px rgba(41, 224, 255, .32); }
.edit-btn {
  border: 1px solid rgba(90, 180, 240, .24); background: transparent; color: #a6bfd6;
}
.edit-btn:hover { border-color: rgba(41, 224, 255, .55); color: #e6f6ff; background: rgba(41, 224, 255, .08); }
.del-btn {
  border: 1px solid rgba(255, 110, 110, .28); background: transparent; color: #e79a9a;
}
.del-btn:hover { border-color: rgba(255, 120, 120, .6); color: #ffc0c0; background: rgba(255, 100, 100, .1); }

/* ---------- 弹层 ---------- */
.mask {
  position: fixed; inset: 0; z-index: 20;
  display: flex; align-items: center; justify-content: center;
  background: radial-gradient(circle at 50% 45%, rgba(5, 14, 24, .72), rgba(2, 5, 10, .9));
  backdrop-filter: blur(7px);
}
.dialog {
  position: relative;
  width: 528px; padding: 24px;
  border-radius: 4px;
  background: linear-gradient(160deg, rgba(14, 36, 57, .97), rgba(7, 16, 27, .98));
  box-shadow:
    0 0 0 1px rgba(41, 224, 255, .17),
    0 42px 100px rgba(0, 0, 0, .78),
    0 0 70px rgba(30, 140, 220, .13);
}
.dialog::before {
  content: '';
  position: absolute; left: 0; right: 0; top: 0; height: 1px;
  background: linear-gradient(90deg, rgba(41, 224, 255, .7), rgba(139, 124, 246, .45), transparent);
}
.dialog::after {
  content: '';
  position: absolute; left: 0; right: 0; bottom: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(41, 224, 255, .22), transparent);
}
.dialog.small { width: 416px; }
.dlg-title {
  position: relative; padding-left: 11px; margin-bottom: 18px;
  font-size: 13.5px; font-weight: 600; letter-spacing: .06em; color: #d8ecfb;
}
.dlg-title::before {
  content: '';
  position: absolute; left: 0; top: 2px; bottom: 2px; width: 2px;
  background: linear-gradient(180deg, #29e0ff, #7b5cf0);
  box-shadow: 0 0 10px rgba(41, 224, 255, .5);
}
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.grid label {
  display: flex; flex-direction: column; gap: 6px;
  font-size: 11.5px; letter-spacing: .04em; color: #93b0c9;
}
.grid input, .grid select {
  height: 36px; padding: 0 11px;
  border: 1px solid rgba(90, 180, 240, .18); border-radius: 3px;
  background: rgba(4, 13, 22, .8); color: #dcefff; font-size: 12.5px;
  outline: none; transition: .16s;
}
.grid input:focus, .grid select:focus {
  border-color: rgba(41, 224, 255, .6);
  box-shadow: 0 0 0 3px rgba(41, 224, 255, .09);
}
.pick { margin-top: 16px; }
.pick input[type=file] { font-size: 11.5px; color: #9fbad2; }
.tip { margin-top: 8px; font-size: 11px; line-height: 1.72; color: #7e9bb5; }
.err {
  margin-top: 12px; padding: 9px 12px;
  border-radius: 3px; border-left: 2px solid rgba(255, 120, 120, .8);
  background: rgba(224, 90, 90, .1); border-top: 1px solid rgba(255, 120, 120, .28);
  border-right: 1px solid rgba(255, 120, 120, .28); border-bottom: 1px solid rgba(255, 120, 120, .28);
  color: #ffb0b0; font-size: 12px;
}
.dlg-actions { margin-top: 20px; display: flex; justify-content: flex-end; gap: 10px; }
.ghost, .primary, .danger {
  height: 33px; padding: 0 18px; cursor: pointer;
  font-size: 12.5px; letter-spacing: .04em;
  border-radius: 3px;
  transition: .16s;
}
.ghost { border: 1px solid rgba(90, 180, 240, .22); background: transparent; color: #a6bfd6; }
.ghost:hover { border-color: rgba(41, 224, 255, .5); color: #e6f6ff; background: rgba(41, 224, 255, .07); }
.primary {
  border: none;
  clip-path: polygon(9px 0, 100% 0, 100% calc(100% - 9px), calc(100% - 9px) 100%, 0 100%, 0 9px);
  background: linear-gradient(100deg, #19cbe8, #5f8ef7 62%, #7b5cf0);
  color: #04121f; font-weight: 700;
}
.primary:hover { filter: brightness(1.12); }
.danger {
  border: none;
  clip-path: polygon(9px 0, 100% 0, 100% calc(100% - 9px), calc(100% - 9px) 100%, 0 100%, 0 9px);
  background: linear-gradient(100deg, #ff6b6b, #d63b58);
  color: #fff; font-weight: 600;
}
.danger:hover { filter: brightness(1.1); }
.primary:disabled, .ghost:disabled, .danger:disabled { opacity: .45; cursor: not-allowed; filter: none; }
.del-text { font-size: 12.5px; line-height: 1.95; color: #c9dcee; }
.del-text b { color: #ffbfbf; font-weight: 600; }
</style>
