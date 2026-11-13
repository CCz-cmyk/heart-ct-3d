<template>
  <div class="volume-viewer">
    <!-- 左侧工具面板 -->
    <aside class="panel" :style="{ width: leftW + 'px' }" @dragstart.prevent>
      <div class="panel-title">工具面板</div>

      <div class="panel-scroll">
        <div class="group">
          <div class="group-title">影像数据源</div>
          <div class="src-row">
            <span class="badge" :class="'badge-' + dataSource">{{ sourceLabel }}</span>
            <span class="src-dim">{{ dims[0] }}×{{ dims[1] }}×{{ dims[2] }}</span>
          </div>
          <div class="src-sub">{{ sliceCount }} 层切片<span v-if="dcmMeta"> · 层厚 {{ dcmMeta.slice_thickness_mm }} mm</span></div>
          <div v-if="dcmMeta" class="src-sub">
            {{ dcmMeta.modality }} · {{ dcmMeta.institution || dcmMeta.manufacturer }}
          </div>
          <div v-if="dcmMeta" class="src-sub">
            像素间距 {{ dcmMeta.pixel_spacing_mm[0] }}×{{ dcmMeta.pixel_spacing_mm[1] }} mm · {{ dcmMeta.bits_allocated }} bit
          </div>
          <div class="presets">
            <button :class="{ active: dataSource !== 'npy' }" :disabled="!dicomAvailable || switching" @click="switchSource('dicom')">DICOM 序列</button>
            <button :class="{ active: dataSource === 'npy' }" :disabled="switching" @click="switchSource('npy')">NPY 体数据</button>
          </div>
          <button class="ghost" :disabled="switching" @click="downloadDicom">下载 DICOM 序列（zip）</button>
          <label class="upload-btn">
            {{ switching ? '正在重建...' : '导入 DICOM（zip）' }}
            <input type="file" accept=".zip" hidden :disabled="switching" @change="onUpload" />
          </label>
          <div v-if="sourceError" class="src-err">{{ sourceError }}</div>
        </div>

        <div class="group">
          <div class="group-title">3D 展示</div>
          <div class="presets">
            <button :class="{ active: renderMode === 'model' }" @click="setRenderMode('model')">解剖模型</button>
            <button :class="{ active: renderMode === 'volume' }" @click="setRenderMode('volume')">体数据渲染</button>
          </div>
          <div class="src-sub">
            {{ renderMode === 'model' ? 'glb 三维心脏模型（通用解剖模型，不随病例变化），可拖动旋转 / 滚轮缩放' : '由 DICOM 体素重建，支持窗宽窗位与剖切' }}
          </div>
          <div v-if="modelError" class="src-err">{{ modelError }}</div>
        </div>

        <div class="group" :class="{ dis: renderMode === 'model' }">
          <div class="group-title">窗宽 / 窗位</div>
          <label>窗宽 {{ ww }}</label>
          <input type="range" draggable="false" min="100" max="2000" v-model.number="ww" @input="scheduleTf" />
          <label>窗位 {{ wc }}</label>
          <input type="range" draggable="false" min="-200" max="800" v-model.number="wc" @input="scheduleTf" />
          <div class="presets">
            <button @click="preset(450, 50)">纵隔窗</button>
            <button @click="preset(1200, -600)">肺窗</button>
            <button @click="preset(800, 300)">骨窗</button>
          </div>
        </div>

        <div class="group" :class="{ dis: renderMode === 'model' }">
          <div class="group-title">体渲染裁剪范围</div>
          <label>X 轴 0 ~ {{ crop.xMax }}</label>
          <input type="range" draggable="false" min="10" :max="dims[0]" v-model.number="crop.xMax" @input="scheduleCrop" />
          <label>Y 轴 0 ~ {{ crop.yMax }}</label>
          <input type="range" draggable="false" min="10" :max="dims[1]" v-model.number="crop.yMax" @input="scheduleCrop" />
          <label>Z 轴 0 ~ {{ crop.zMax }}</label>
          <input type="range" draggable="false" min="10" :max="dims[2]" v-model.number="crop.zMax" @input="scheduleCrop" />
          <button class="ghost" @click="resetCrop">重置剖切</button>
        </div>

        <div class="group">
          <div class="group-title">MPR 切面位置</div>
          <label>轴位 Z {{ slice.z }}</label>
          <input type="range" draggable="false" :min="0" :max="dims[2]-1" v-model.number="slice.z" @input="scheduleSlices('z')" />
          <label>冠状 Y {{ slice.y }}</label>
          <input type="range" draggable="false" :min="0" :max="dims[1]-1" v-model.number="slice.y" @input="scheduleSlices('y')" />
          <label>矢状 X {{ slice.x }}</label>
          <input type="range" draggable="false" :min="0" :max="dims[0]-1" v-model.number="slice.x" @input="scheduleSlices('x')" />
        </div>

        <div class="group">
          <button class="ghost" @click="resetCamera">重置视图</button>
          <button class="ghost" :class="{ active: measureMode }" @click="toggleMeasure">切面测量（三视图）</button>
        </div>

        <div v-if="lesions.length" class="group">
          <div class="group-title">检出病灶（{{ lesions.length }}）</div>
          <div v-for="(L, i) in lesions" :key="L.id || ('auto' + i)" class="lesion-item">
            <div class="lesion-name">{{ i + 1 }}. {{ L.name }}<span class="src-tag" :class="L.source === 'auto' ? 'auto' : 'manual'">{{ L.source === 'auto' ? 'AI 检出' : '人工标注' }}</span></div>
            <div class="lesion-meta">{{ L.type }} · 置信度 {{ L.confidence == null ? '未获得' : (L.confidence * 100).toFixed(1) + '%' }}</div>
            <div class="lesion-meta">坐标({{ L.cx.toFixed(0) }}, {{ L.cy.toFixed(0) }}, {{ L.cz.toFixed(0) }}) · 大小 {{ (L.radius * 2 * (spacing[0] || 1)).toFixed(0) }}mm</div>
          </div>
        </div>
      </div>
    </aside>

    <div class="splitter" title="拖动调整工具面板宽度" @pointerdown.prevent="startSplit('left', $event)"></div>

    <!-- 主视图区 -->
    <main class="scene">
      <div ref="main3d" class="main3d">
        <i class="hud tl" aria-hidden="true"></i>
        <i class="hud tr" aria-hidden="true"></i>
        <i class="hud bl" aria-hidden="true"></i>
        <i class="hud br" aria-hidden="true"></i>
        <div v-show="renderMode === 'volume'" class="anno-layer">
          <span v-for="a in annotations" :key="a.text" class="anno" :style="a.style">{{ a.text }}</span>
        </div>
        <div ref="model3d" class="model3d" v-show="renderMode === 'model'"></div>
        <div v-if="renderMode === 'model' && modelLoading" class="model-mask">
          <div class="mm-title">正在加载三维心脏模型</div>
          <div class="mm-bar"><div class="mm-fill" :style="{ width: modelPct + '%' }"></div></div>
          <div class="mm-sub">{{ modelPct }}% · 约 73 MB / 150 万面</div>
        </div>
        <div v-if="renderMode === 'model' && !modelLoading" class="model-tip">
          拖动旋转 · 滚轮缩放 · {{ modelCustom ? '本病例专属模型' : '通用解剖模型（无专属文件时）' }}
        </div>
      </div>
      <div class="mpr-bar">
        <span class="mpr-bar-title">剖切视图</span>
        <div class="seg">
          <button :class="{ active: sliceView === '3d' }" @click="setSliceView('3d')">三方向 3D 剖切</button>
          <button :class="{ active: sliceView === '2d' }" @click="setSliceView('2d')">2D 精读 / 测量</button>
        </div>
        <span class="mpr-bar-hint">
          {{ sliceView === '3d' ? '下方三图为该病例三维模型的实时剖切截面（剖面填充为实心心肌），随左侧「MPR 切面位置」滑块联动' : '标准 MPR 三视图，支持切面测距' }}
        </span>
      </div>
      <div v-show="sliceView === '3d'" class="slice3d-wrap">
        <ModelSlice3D
          v-if="volumeData"
          :case-id="caseId"
          :volume="volumeData"
          :slice-x="slice.x"
          :slice-y="slice.y"
          :slice-z="slice.z"
          :lesions="lesions"
        />
        <div v-else class="slice-placeholder">正在准备体数据…</div>
      </div>
      <div v-show="sliceView === '2d'" class="mprs">
        <div class="mpr-window">
          <div class="mpr-title">轴位 (Z={{ slice.z }})</div>
          <div ref="mprAxial" class="mpr-canvas" @click="onMprClick($event, 'axial')" @wheel="clearOverlay()"></div>
          <canvas ref="overlayAxial" class="overlay"></canvas>
        </div>
        <div class="mpr-window">
          <div class="mpr-title">冠状 (Y={{ slice.y }})</div>
          <div ref="mprCoronal" class="mpr-canvas" @click="onMprClick($event, 'coronal')" @wheel="clearOverlay()"></div>
          <canvas ref="overlayCoronal" class="overlay"></canvas>
        </div>
        <div class="mpr-window">
          <div class="mpr-title">矢状 (X={{ slice.x }})</div>
          <div ref="mprSagittal" class="mpr-canvas" @click="onMprClick($event, 'sagittal')" @wheel="clearOverlay()"></div>
          <canvas ref="overlaySagittal" class="overlay"></canvas>
        </div>
      </div>
    </main>

    <div class="splitter" title="拖动调整 AI 报告栏宽度" @pointerdown.prevent="startSplit('right', $event)"></div>

    <!-- 右侧报告 -->
    <aside class="report" :style="{ width: rightW + 'px' }">
      <div class="report-head">
        <div class="panel-title rp-title">AI 结构化报告</div>
        <span class="ai-badge" :class="{ on: aiOnline }">{{ aiOnline ? '模型在线' : '未连接' }}</span>
      </div>

      <div class="group ai-group">
        <div class="ai-head">
          <span class="ai-dot" :class="{ on: aiOnline, busy: analyzing }"></span>
          <span class="ai-title">AI 推理流水线</span>
        </div>
        <div class="ai-model-line">{{ aiModel || '未连接模型' }}</div>
        <div class="ai-stages">
          <div v-for="s in aiStages" :key="s.key" class="ai-stage" :class="s.state">
            <span class="ai-stage-dot"></span>
            <span class="ai-stage-name">{{ s.text }}</span>
            <span class="ai-stage-state">{{ stageLabel(s.state) }}</span>
          </div>
        </div>
        <div v-if="analyzing" class="ai-bar">
          <div class="ai-fill" :style="{ width: progress + '%' }"></div>
        </div>
        <div class="ai-sub" v-if="analyzing">{{ progressText }} · 已用 {{ elapsed }}s</div>
        <div class="ai-sub" v-else-if="aiResult">
          上次推理 {{ aiResult.ai_meta.calls }} 次 · {{ aiResult.ai_meta.elapsed_sec }}s · {{ aiResult.ai_meta.usage_total_tokens }} tokens<span class="ai-saved" v-if="aiSavedAt"> · {{ aiSavedAt }} 已存档</span>
        </div>
        <button class="primary ai-btn" :disabled="analyzing" @click="startAnalyze">
          {{ analyzing ? '推理中…' : (aiResult ? '重新推理' : '开始 AI 分析') }}
        </button>
      </div>

      <div v-if="!aiResult" class="empty">
        <template v-if="analyzing">
          <span class="empty-spin"></span>
          大模型推理中，请稍候…<br />
          当前阶段：{{ progressText || '准备中' }}
        </template>
        <template v-else>
          点击左侧「开始 AI 分析」<br />
          系统会实测病灶球体内外的 HU 分布，<br />
          再把特征送入大模型推理分割参数
        </template>
      </div>

      <template v-else>
        <div class="ai-sum">
          <div class="ai-sum-item"><span class="k">模型</span><span class="v">{{ aiResult.ai_meta.model }}</span></div>
          <div class="ai-sum-item"><span class="k">推理</span><span class="v">{{ aiResult.ai_meta.calls }} 次调用 / {{ aiResult.ai_meta.elapsed_sec }}s</span></div>
          <div class="ai-sum-item"><span class="k">用量</span><span class="v">{{ aiResult.ai_meta.usage_total_tokens }} tokens</span></div>
          <div class="ai-sum-item"><span class="k">体数据</span><span class="v">{{ aiResult.ai_meta.volume_file }} · {{ (aiResult.ai_meta.volume_shape || []).join('×') }}</span></div>
        </div>

        <div v-if="!aiResult.lesions.length" class="empty">
          自动检出未发现符合条件的高密度病灶<br />
          <span class="empty-hint">{{ aiResult.conclusion }}</span>
        </div>

        <div v-for="(L, i) in aiResult.lesions" :key="i" class="ai-card">
          <div class="ai-card-top">
            <span class="ai-idx">{{ i + 1 }}</span>
            <span class="ai-name">{{ L.name }}</span>
          </div>
          <div class="ai-tags">
            <span v-if="L.source === 'auto'" class="ai-auto-tag">自动检出 · 无人工标注</span>
            <span v-if="L.polarity === 'low'" class="ai-low-tag">低密度灶 · 反向阈值（低于阈值判为灶体）</span>
          </div>
          <div v-if="L.ai_error" class="ai-fail">
            <span class="ai-fail-tag">模型调用失败</span>
            <span class="ai-fail-text">{{ L.ai_error }}</span>
          </div>

          <div class="ai-row">
            <span class="k">病灶类型</span>
            <span class="v">{{ L.type }}</span>
          </div>

          <div class="ai-row">
            <span class="k">AI 判读</span>
            <span class="v">
              {{ L.severity || '—' }}
              <span v-if="L.source === 'db' && L.agree_with_label === false" class="ai-diff">与标注不一致</span>
            </span>
          </div>

          <div class="ai-row">
            <span class="k">置信度</span>
            <div class="ai-conf-bar"><div class="ai-conf-fill" :style="{ width: confPct(L) }"></div></div>
            <span class="ai-conf-num" :class="{ na: L.confidence == null }">{{ confText(L) }}</span>
          </div>

          <div class="ai-row">
            <span class="k">分割参数</span>
            <span class="v">阈值 {{ L.threshold_hu }} HU · 层厚 {{ L.slice_thickness_mm }} mm
              <span v-if="L.ai_error" class="ai-fallback">按规则兜底</span></span>
          </div>

          <div class="ai-vol">
            <div v-for="m in L.measures" :key="m.source" class="ai-vol-item" :class="{ hi: m.source === 'ai' }">
              <div class="ai-vol-src">{{ m.source === 'ai' ? 'AI 参数实测' : '临床默认实测' }}</div>
              <div class="ai-vol-num">{{ m.volume_mm3.toFixed(1) }}<span>mm³</span></div>
              <div class="ai-vol-err" :class="errClass(L, m)">{{ errText(L, m) }}</div>
            </div>
            <div v-if="L.truth_volume_mm3" class="ai-vol-item truth">
              <div class="ai-vol-src">仿真真值</div>
              <div class="ai-vol-num">{{ L.truth_volume_mm3.toFixed(1) }}<span>mm³</span></div>
              <div class="ai-vol-err">r = {{ L.radius }} 体素</div>
            </div>
          </div>

          <div v-if="L.notes && L.notes.length" class="ai-note">
            <div v-for="(n, k) in L.notes" :key="k" class="ai-note-line">· {{ n }}</div>
          </div>

          <div v-if="L.finding" class="ai-finding">{{ L.finding }}</div>
          <div v-if="L.suggest" class="ai-suggest">{{ L.suggest }}</div>

          <details class="ai-raw">
            <summary>查看本次模型原文与提示词</summary>
            <div class="ai-raw-block">
              <div class="ai-raw-label">提示词（实际发送给模型的全文）</div>
              <pre>{{ rawPrompt(L) }}</pre>
            </div>
            <div class="ai-raw-block">
              <div class="ai-raw-label">模型原文{{ L.ai_error ? '（本次未获取到内容）' : '' }}</div>
              <pre>{{ rawAnswer(L) }}</pre>
            </div>
          </details>
        </div>

        <div class="report-note">* 数字体模仿真数据，本文为 AI 辅助科研演示，不具备临床诊断效力</div>
      </template>
    </aside>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { api } from '../api'
import ModelSlice3D from './ModelSlice3D.vue'

import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

import '@kitware/vtk.js/Rendering/Profiles/All'

import vtkGenericRenderWindow from '@kitware/vtk.js/Rendering/Misc/GenericRenderWindow'
import vtkVolume from '@kitware/vtk.js/Rendering/Core/Volume'
import vtkVolumeMapper from '@kitware/vtk.js/Rendering/Core/VolumeMapper'
import vtkImageData from '@kitware/vtk.js/Common/DataModel/ImageData'
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray'
import vtkColorTransferFunction from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction'
import vtkPiecewiseFunction from '@kitware/vtk.js/Common/DataModel/PiecewiseFunction'
import vtkSphereSource from '@kitware/vtk.js/Filters/Sources/SphereSource'
import vtkActor from '@kitware/vtk.js/Rendering/Core/Actor'
import vtkMapper from '@kitware/vtk.js/Rendering/Core/Mapper'
import vtkImageCropFilter from '@kitware/vtk.js/Filters/General/ImageCropFilter'
import vtkCoordinate from '@kitware/vtk.js/Rendering/Core/Coordinate'
import vtkImageSlice from '@kitware/vtk.js/Rendering/Core/ImageSlice'
import vtkImageMapper from '@kitware/vtk.js/Rendering/Core/ImageMapper'

const props = defineProps({ caseId: { type: [Number, String], required: true } })

const main3d = ref(null)
const mprAxial = ref(null)
const mprCoronal = ref(null)
const mprSagittal = ref(null)
const overlayAxial = ref(null)
const overlayCoronal = ref(null)
const overlaySagittal = ref(null)

// 两侧面板宽度（由分隔条拖动调整）
const leftW = ref(252)
const rightW = ref(384)
let splitSide = null
let splitStartX = 0
let splitStartW = 0
let splitRaf = 0

/** 拖拽过程中合并重排，避免每帧多次 resize */
function scheduleLayout() {
  if (splitRaf) return
  splitRaf = requestAnimationFrame(() => {
    splitRaf = 0
    mainGwin?.resize()
    mainGwin?.getRenderWindow().render()
    resizeModel()
  })
}

function startSplit(side, e) {
  splitSide = side
  splitStartX = e.clientX
  splitStartW = side === 'left' ? leftW.value : rightW.value
  document.body.style.cursor = 'col-resize'
  window.addEventListener('pointermove', onSplitMove)
  window.addEventListener('pointerup', stopSplit)
}

function onSplitMove(e) {
  const dx = e.clientX - splitStartX
  if (splitSide === 'left') {
    leftW.value = Math.min(400, Math.max(190, splitStartW + dx))
  } else {
    rightW.value = Math.min(640, Math.max(280, splitStartW - dx))
  }
  scheduleLayout()
}

function stopSplit() {
  splitSide = null
  document.body.style.cursor = ''
  window.removeEventListener('pointermove', onSplitMove)
  window.removeEventListener('pointerup', stopSplit)
}

const dims = ref([256, 256, 192])
const ww = ref(600)
const wc = ref(120)
const crop = reactive({ xMax: 256, yMax: 256, zMax: 192 })
const slice = reactive({ x: 128, y: 128, z: 96 })
const dataSource = ref('auto')        // npy | dicom | upload
const dcmMeta = ref(null)
const dicomAvailable = ref(true)
const sliceCount = ref(192)
const spacing = ref([1, 1, 1])
const switching = ref(false)
const sourceError = ref('')
const analyzing = ref(false)
const progress = ref(0)
const progressText = ref('')
const elapsed = ref(0)
const lesions = ref([])
const report = ref('')
const aiResult = ref(null)
const aiSavedAt = ref('')
const aiOnline = ref(false)
const aiModel = ref('')
const aiStages = ref([
  { key: 'volume', text: '读取体数据', state: 'idle' },
  { key: 'feature', text: '实测 HU 特征', state: 'idle' },
  { key: 'llm', text: '大模型推理', state: 'idle' },
  { key: 'verify', text: '结果校验', state: 'idle' },
])
const measureMode = ref(false)
const measurePts = []
const measureTexts = { axial: [], coronal: [], sagittal: [] }
const measureLast = { axial: null, coronal: null, sagittal: null }

// 3D 展示模式：model = glb 解剖模型（Three.js），volume = DICOM 体数据体绘制（VTK）
const renderMode = ref('model')
const model3d = ref(null)
const modelLoading = ref(false)
const modelPct = ref(0)
const modelError = ref('')
const modelCustom = ref(false)
let threeCtx = null

// 下方剖面区：3d = 三方向 3D 剖切（专属病例网格 + 切口 CT 切面）/ 2d = 传统 MPR
const sliceView = ref('3d')
const volumeData = ref(null)          // { dims:[nx,ny,nz], scalars:Float32Array }

let imageData = null
let mainGwin = null
let volumeActor = null
let volumeMapper = null
let cropFilter = null
let lesionActors = []
let mprWindows = {}
let annoResizeObserver = null

const sourceLabel = computed(() => {
  if (dataSource.value === 'dicom') return 'DICOM'
  if (dataSource.value === 'upload') return 'DICOM·导入'
  return 'NPY'
})

/** 扫描体数据，取心脏组织（>= thr HU）的包围盒（采样步长 2，约 1.5M 次比较，10ms 级） */
function computeHeartBBox(dims, scalars, thr = 110) {
  const [nx, ny, nz] = dims
  let minX = nx, minY = ny, minZ = nz, maxX = -1, maxY = -1, maxZ = -1
  const step = 2
  for (let z = 0; z < nz; z += step) {
    const base = z * ny * nx
    for (let y = 0; y < ny; y += step) {
      const rowOff = base + y * nx
      for (let x = 0; x < nx; x += step) {
        if (scalars[rowOff + x] >= thr) {
          if (x < minX) minX = x
          if (x > maxX) maxX = x
          if (y < minY) minY = y
          if (y > maxY) maxY = y
          if (z < minZ) minZ = z
          if (z > maxZ) maxZ = z
        }
      }
    }
  }
  if (maxX < 0) return { min: [nx * 0.2, ny * 0.2, nz * 0.2], max: [nx * 0.8, ny * 0.8, nz * 0.8] }
  return { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] }
}

async function loadVolume(src = 'auto') {  const res = await api.getVolume(props.caseId, src)
  const d = res.dims
  dims.value = d
  dataSource.value = res.source
  sliceCount.value = res.slices
  spacing.value = res.spacing && res.spacing.length === 3 ? res.spacing : [1, 1, 1]
  if (res.source === 'dicom' || res.source === 'upload') {
    try {
      dcmMeta.value = await api.getDicomInfo(props.caseId, res.source)
      dicomAvailable.value = true
    } catch (e) {
      dcmMeta.value = null
    }
  } else {
    dcmMeta.value = null
  }
  const scalars = new Float32Array(res.buffer)
  // 心脏组织（>=110 HU）的包围盒：用于把三维模型放到该病例体数据中的真实位置
  const hb = computeHeartBBox([d[0], d[1], d[2]], scalars, 110)
  volumeData.value = { dims: [d[0], d[1], d[2]], scalars, heartBBox: hb }
  imageData = vtkImageData.newInstance()
  imageData.setDimensions([d[0], d[1], d[2]])
  imageData.setOrigin([0, 0, 0])
  imageData.setSpacing([1, 1, 1])   // 数据坐标 = 像素索引；真实 mm 由 spacing 换算到测量/界面
  const da = vtkDataArray.newInstance({ name: 'scalars', values: scalars })
  imageData.getPointData().setScalars(da)

  crop.xMax = d[0]; crop.yMax = d[1]; crop.zMax = d[2]
  slice.x = Math.floor(d[0] / 2); slice.y = Math.floor(d[1] / 2); slice.z = Math.floor(d[2] / 2)

  setupMain()
  setupMpr(mprAxial.value, 'axial', 2, slice.z)   // 2 = K 轴位
  setupMpr(mprCoronal.value, 'coronal', 1, slice.y)  // 1 = J 冠状
  setupMpr(mprSagittal.value, 'sagittal', 0, slice.x)  // 0 = I 矢状
  updateTfAndMpr()
  updateSlices()
}

/** 切换影像来源：销毁旧渲染上下文并重建（DICOM 序列 ↔ NPY 体数据） */
async function switchSource(src) {
  if (switching.value || src === dataSource.value) return
  switching.value = true
  sourceError.value = ''
  try {
    destroyViewer()
    await loadVolume(src)
    if (lesions.value.length) markLesions()
  } catch (e) {
    sourceError.value = e.message || '加载失败'
    // 失败时回退到原来的来源，避免白屏
    try {
      destroyViewer()
      await loadVolume('npy')
    } catch (e2) { /* 忽略 */ }
  } finally {
    switching.value = false
  }
}

/** 按病例挑选模型文件：/models/case_XX.glb 存在就用（专属），否则回退通用 heart_model.glb */
async function pickCaseModelUrl() {
  const names = [`/models/case_${String(props.caseId).padStart(2, '0')}.glb`, `/models/case_${props.caseId}.glb`]
  for (const n of names) {
    try {
      const r = await fetch(n, { method: 'HEAD' })
      const ct = (r.headers.get('content-type') || '').toLowerCase()
      if (r.ok && !ct.includes('text/html')) return { url: n, custom: true }
    } catch (e) { /* 继续尝试 */ }
  }
  return { url: '/models/heart_model.glb', custom: false }
}

/** 初始化 glb 解剖模型渲染（Three.js），已初始化则复用 */
async function initModelView() {
  if (threeCtx || !model3d.value) return
  const el = model3d.value
  const w = el.clientWidth || 900
  const h = el.clientHeight || 620

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(w, h)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.15
  el.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.add(new THREE.HemisphereLight(0xeaf4ff, 0x1d2a3c, 2.4))
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(1.6, 2.4, 3.2); scene.add(key)
  const fill = new THREE.DirectionalLight(0xffcfc0, 1.1); fill.position.set(-3, -0.6, 1.6); scene.add(fill)
  const rim = new THREE.DirectionalLight(0x8fc4ff, 0.9); rim.position.set(0.4, -3.2, -2.4); scene.add(rim)
  const back = new THREE.DirectionalLight(0xffffff, 0.5); back.position.set(-1.2, 1.4, -3); scene.add(back)

  const camera = new THREE.PerspectiveCamera(32, w / h, 0.01, 200)
  camera.position.set(0.1, 0.35, 3.95)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.rotateSpeed = 0.8
  controls.zoomSpeed = 0.9
  controls.minDistance = 0.8
  controls.maxDistance = 8
  controls.target.set(0, 0, 0)

  // 自定义滚轮缩放：OrbitControls 默认每格仅约 5%（实测三格 3.97→3.36），手感偏钝，
  // 这里按 wheel delta 定量缩放，与体数据渲染那套的操作手感对齐
  controls.enableZoom = false
  const onWheel = (e) => {
    e.preventDefault()
    const dz = Math.max(-150, Math.min(150, e.deltaY))
    const factor = Math.pow(1.0015, dz)
    const v = camera.position.clone().sub(controls.target)
    const len = v.length()
    const next = Math.min(8, Math.max(0.9, len * factor))
    v.setLength(next)
    camera.position.copy(controls.target).add(v)
    controls.update()
  }
  el.addEventListener('wheel', onWheel, { passive: false })

  threeCtx = { renderer, scene, camera, controls, root: null, raf: null, el, onWheel }

  modelLoading.value = true
  modelPct.value = 0
  modelError.value = ''
  const loader = new GLTFLoader()
  const picked = await pickCaseModelUrl()
  modelCustom.value = picked.custom
  loader.load(
    picked.url,
    (gltf) => {
      if (!threeCtx) return
      const root = gltf.scene
      // Blender 导出的材质金属度为 1，没有环境贴图时会整体发黑，这里降到柔和反射
      root.traverse((o) => {
        if (!o.isMesh) return
        const mats = Array.isArray(o.material) ? o.material : [o.material]
        mats.forEach((m) => {
          if (!m) return
          m.metalness = 0.12
          m.roughness = 0.7
          if (m.map) { m.map.colorSpace = THREE.SRGBColorSpace; m.map.anisotropy = 4 }
          m.needsUpdate = true
        })
      })
      const box = new THREE.Box3().setFromObject(root)
      const size = box.getSize(new THREE.Vector3())
      const center = box.getCenter(new THREE.Vector3())
      const maxDim = Math.max(size.x, size.y, size.z) || 1
      root.position.set(-center.x, -center.y, -center.z)
      const wrapper = new THREE.Group()
      wrapper.add(root)
      wrapper.scale.setScalar(1.9 / maxDim)
      scene.add(wrapper)
      threeCtx.root = wrapper
      threeCtx.modelBox = { size, center, maxDim }
      threeCtx.lesionGroup = new THREE.Group()
      wrapper.add(threeCtx.lesionGroup)
      paintModelLesions()

      modelLoading.value = false
      resizeModel()
      camera.lookAt(0, 0, 0)
      controls.update()
      renderer.render(scene, camera)

      const tick = () => {
        if (!threeCtx) return
        if (renderMode.value === 'model') {
          controls.update()
          renderer.render(scene, camera)
        }
        threeCtx.raf = requestAnimationFrame(tick)
      }
      threeCtx.raf = requestAnimationFrame(tick)
    },
    (e) => { if (e && e.total) modelPct.value = Math.min(99, Math.round((e.loaded / e.total) * 100)) },
    (err) => {
      modelLoading.value = false
      modelError.value = '三维模型加载失败：' + (err && err.message ? err.message : '检查 public/models/ 下的模型文件')
    },
  )
}

/** 把该病例的病灶按解剖位置标到三维模型上（体素包围盒 → 模型包围盒的近似映射） */
function paintModelLesions() {
  if (!threeCtx || !threeCtx.lesionGroup || !threeCtx.modelBox) return
  const group = threeCtx.lesionGroup
  while (group.children.length) {
    const c = group.children.pop()
    c.geometry?.dispose?.(); c.material?.dispose?.()
  }
  const hb = volumeData.value?.heartBBox
  if (!hb || !lesions.value.length) return
  if (renderMode.value !== 'model') return
  const { size, center, maxDim } = threeCtx.modelBox
  const spanX = hb.max[0] - hb.min[0] || 1
  const spanY = hb.max[1] - hb.min[1] || 1
  const spanZ = hb.max[2] - hb.min[2] || 1
  const spanLong = Math.max(spanX, spanY, spanZ)
  lesions.value.forEach(L => {
    const t = [
      Math.min(1, Math.max(0, (Number(L.cx) - hb.min[0]) / spanX)),
      Math.min(1, Math.max(0, (Number(L.cy) - hb.min[1]) / spanY)),
      Math.min(1, Math.max(0, (Number(L.cz) - hb.min[2]) / spanZ)),
    ]
    // 体素轴 → 模型轴：x 对 x（左向反转），体素 z（头脚）对模型 y（上下），体素 y（前后）对模型 z
    const p = new THREE.Vector3(
      size.x * (0.5 - t[0]) * 0.85,
      size.y * (0.5 - t[2]) * 0.85,
      size.z * (t[1] - 0.5) * 0.85,
    )
    const r = Math.max((Number(L.radius) || 8) / spanLong * maxDim * 0.3, maxDim * 0.006)
    // AI 自动检出用青色，人工标注用琥珀色，一眼能区分来源
    const isAuto = (L.source || 'manual') === 'auto'
    const ball = new THREE.Mesh(
      new THREE.SphereGeometry(r, 20, 14),
      new THREE.MeshBasicMaterial({ color: isAuto ? 0x29e0ff : 0xffd24a, depthTest: false, depthWrite: false }),
    )
    ball.position.copy(p)
    ball.renderOrder = 9
    group.add(ball)
    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(r * 1.8, 18, 12),
      new THREE.MeshBasicMaterial({ color: isAuto ? 0x1a9fc4 : 0xff9a00, transparent: true, opacity: 0.3, depthTest: false, depthWrite: false }),
    )
    halo.position.copy(p)
    halo.renderOrder = 8
    group.add(halo)
  })
}

function resizeModel() {
  if (!threeCtx || !threeCtx.el) return
  const w = threeCtx.el.clientWidth
  const h = threeCtx.el.clientHeight
  if (!w || !h) return
  threeCtx.renderer.setSize(w, h)
  threeCtx.camera.aspect = w / h
  threeCtx.camera.updateProjectionMatrix()
}

function disposeModel() {
  if (!threeCtx) return
  if (threeCtx.raf) cancelAnimationFrame(threeCtx.raf)
  threeCtx.root?.traverse((o) => {
    if (!o.isMesh) return
    o.geometry?.dispose?.()
    const mats = Array.isArray(o.material) ? o.material : [o.material]
    mats.forEach((m) => { if (m) { m.map?.dispose?.(); m.dispose?.() } })
  })
  threeCtx.el?.removeEventListener('wheel', threeCtx.onWheel)
  threeCtx.controls?.dispose?.()
  threeCtx.renderer.dispose()
  threeCtx.renderer.domElement?.remove()
  threeCtx = null
}

function setRenderMode(m) {
  if (m === renderMode.value) return
  renderMode.value = m
  clearOverlay()
  if (m === 'model') {
    stopAnnoLoop()
    nextTick(() => { initModelView().then(() => paintModelLesions()) })
  } else {
    startAnnoLoop()
    mainGwin?.resize()
    mainGwin?.getRenderWindow().render()
  }
}

/** 释放 vtk 渲染器与容器内的 canvas，保留标注层 DOM */
function destroyViewer() {
  stopAnnoLoop()
  if (annoResizeObserver) { annoResizeObserver.disconnect(); annoResizeObserver = null }
  clearOverlay()
  for (const k of Object.keys(mprWindows)) {
    try { mprWindows[k].gwin.delete() } catch (e) { /* ignore */ }
  }
  mprWindows = {}
  if (mainGwin) {
    try { mainGwin.delete() } catch (e) { /* ignore */ }
    mainGwin = null
  }
  volumeActor = null; volumeMapper = null; cropFilter = null; imageData = null
  lesionActors = []
  for (const el of [main3d.value, mprAxial.value, mprCoronal.value, mprSagittal.value]) {
    if (!el) continue
    el.querySelectorAll('canvas').forEach((c) => {
      // .model3d 里的画布属于 Three.js 解剖模型，与 vtk 无关；误删后 threeCtx 仍在，
      // initModelView() 会早退不再重建，切回「解剖模型」就是一片空白
      if (c.closest('.model3d')) return
      c.remove()
    })
  }
}

async function downloadDicom() {
  try {
    const blob = await api.downloadDicomZip(props.caseId, dataSource.value)
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `case_${props.caseId}_dicom_series.zip`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  } catch (e) {
    sourceError.value = e.message || '下载失败'
  }
}

async function onUpload(ev) {
  const file = ev.target.files && ev.target.files[0]
  if (!file) return
  switching.value = true
  sourceError.value = ''
  try {
    await api.uploadDicom(props.caseId, file)
    destroyViewer()
    await loadVolume('upload')
    if (lesions.value.length) markLesions()
  } catch (e) {
    sourceError.value = e.message || '导入失败'
  } finally {
    switching.value = false
    ev.target.value = ''
  }
}

function setupMain() {
  mainGwin = vtkGenericRenderWindow.newInstance()
  mainGwin.setContainer(main3d.value)
  mainGwin.resize()

  volumeMapper = vtkVolumeMapper.newInstance()
  // 采样步长按体素规模自适应：256³（1 mm）用 0.3 体素=0.3 mm；
  // 512×512×384（0.5 mm 高清）用 0.6 体素，物理步长同为 0.3 mm，并放宽最大步数避免断层
  const voxCount = dims.value[0] * dims.value[1] * dims.value[2]
  const isHighRes = voxCount > 4e7
  volumeMapper.setSampleDistance(isHighRes ? 0.6 : 0.3)
  // 上限只截断、不影响实际步数，放宽后避免"所需步数超过上限"的采样截断
  volumeMapper.setMaximumSamplesPerRay(isHighRes ? 3000 : 1500)
  cropFilter = vtkImageCropFilter.newInstance()
  cropFilter.setInputData(imageData)
  applyCrop()

  volumeActor = vtkVolume.newInstance()
  volumeActor.setMapper(volumeMapper)
  mainGwin.getRenderer().addVolume(volumeActor)

  // 初始视角：正面俯视（心房上、心室下、心尖朝左下），结构关系严格符合解剖图
  setAnatomyView()
  startAnnoLoop()

  // 容器尺寸变化（窗口/页面缩放/面板折叠）时同步 vtk 视口，保证标注与3D始终对齐
  if (typeof ResizeObserver !== 'undefined') {
    annoResizeObserver = new ResizeObserver(() => {
      clearOverlay()
      mainGwin?.resize()
      mainGwin?.getRenderWindow().render()
    })
    annoResizeObserver.observe(main3d.value)
  }
}

// 统一解剖视角：正面平视（y增大=后方，相机置于 y 小侧=正前方）
// 推导：viewDir=(0,+,0) × viewUp=(0,0,1) → right=+x（右）、屏幕up=+z（头侧在上）
// 结果：屏幕右=+x、屏幕上=+z、心尖(小z小x)在左下方、主动脉弓(大z)在上方 → 与解剖教学图一致
// 世界坐标缩放：相机与标注按 256 网格基准编写，高清体数据（512×512×384）世界坐标为其 2 倍
function worldScale() {
  return (dims.value[0] || 256) / 256
}

function setAnatomyView() {
  const camera = mainGwin.getRenderer().getActiveCamera()
  const s = worldScale()
  camera.setFocalPoint(120 * s, 124 * s, 88 * s)        // 心脏质心
  camera.setPosition(120 * s, (124 - 320) * s, 88 * s)  // 正前方、与心脏同高（纯平视）
  camera.setViewUp(0, 0, 1)                 // 世界z（头侧）指向屏幕上方
  camera.setParallelProjection(false)
  camera.zoom(1.0)
  mainGwin.getRenderer().resetCameraClippingRange()
  mainGwin.getRenderWindow().render()
}

// 3D 结构标注（锚点按 256 网格基准的真实解剖位置，高清例自动按 worldScale 换算）
const annotations = reactive([
  { text: '心尖', pos: [84, 88, 28], style: {} },
  { text: '左心室', pos: [108, 106, 42], style: {} },
  { text: '右心室', pos: [140, 110, 84], style: {} },
  { text: '主动脉弓', pos: [106, 126, 136], style: {} },
  { text: '前降支', pos: [90, 78, 64], style: {} },
  { text: '左心房', pos: [106, 124, 128], style: {} },
  { text: '右心房', pos: [144, 138, 126], style: {} },
])

// 标注跟随循环（每帧更新，不依赖 vtk 事件 API）
let annoRaf = null
const annoCoord = vtkCoordinate.newInstance()
annoCoord.setCoordinateSystemToWorld()
function startAnnoLoop() {
  stopAnnoLoop()
  const tick = () => {
    updateAnnotations()
    annoRaf = requestAnimationFrame(tick)
  }
  annoRaf = requestAnimationFrame(tick)
}
function stopAnnoLoop() {
  if (annoRaf) { cancelAnimationFrame(annoRaf); annoRaf = null }
}

function updateAnnotations() {
  try {
    if (!mainGwin) return
    const renderer = mainGwin.getRenderer()
    const cw = main3d.value ? main3d.value.clientWidth : 0
    const ch = main3d.value ? main3d.value.clientHeight : 0
    if (!cw || !ch) return
    // vtk framebuffer 尺寸 = CSS 尺寸 × devicePixelRatio，投影返回 framebuffer 像素，
    // 必须除以 dpr 转为 CSS 像素，否则屏幕缩放≠100% 时标注整体偏右偏下
    const dpr = window.devicePixelRatio || 1
    const vw = cw * dpr
    const vh = ch * dpr
    const s = worldScale()
    for (const a of annotations) {
      annoCoord.setValue(a.pos[0] * s, a.pos[1] * s, a.pos[2] * s)
      const disp = annoCoord.getComputedViewportValue(renderer)
      if (!disp || disp[0] < 0 || disp[1] < 0 || disp[0] >= vw || disp[1] >= vh) {
        a.style = { display: 'none' }
        continue
      }
      a.style = {
        left: disp[0] / dpr + 'px',
        top: ch - disp[1] / dpr + 'px',   // vtk viewport y 从底部起算，翻转后与 CSS top 一致
        display: 'block',
      }
    }
  } catch (e) {
    /* 标注层异常不影响 3D 渲染 */
  }
}

function applyCrop() {
  if (!cropFilter) return
  cropFilter.setCroppingPlanes(0, crop.xMax, 0, crop.yMax, 0, crop.zMax)
  cropFilter.update()
  if (volumeMapper) {
    volumeMapper.setInputData(cropFilter.getOutputData())
    mainGwin?.getRenderWindow().render()
  }
}

function preset(cw, cc) {
  ww.value = cw; wc.value = cc
  updateTfAndMpr()
}

function updateTfAndMpr() {
  if (!imageData) return
  const ctMin = wc.value - ww.value / 2
  const ctMax = wc.value + ww.value / 2
  // MPR 切片同步窗宽窗位（此前缺失导致切片默认窗口与CT值不匹配、显示黑屏）
  for (const k of Object.keys(mprWindows)) {
    mprWindows[k].actor.getProperty().setColorWindow(ww.value)
    mprWindows[k].actor.getProperty().setColorLevel(wc.value)
  }
  const c = vtkColorTransferFunction.newInstance()
  c.addRGBPoint(-1024, 0.0, 0.0, 0.0)
  c.addRGBPoint(ctMin, 0.12, 0.10, 0.18)
  c.addRGBPoint(70, 0.30, 0.25, 0.55)       // 心腔血液：蓝灰色
  c.addRGBPoint(150, 0.82, 0.16, 0.16)      // 心肌：红色
  c.addRGBPoint(300, 0.98, 0.90, 0.70)      // 冠脉/钙化：暖白
  c.addRGBPoint(ctMax, 1.0, 1.0, 1.0)
  for (const L of lesions.value) {
    c.addRGBPoint(L.ct_value, 1.0, 0.12, 0.12)
  }
  const o = vtkPiecewiseFunction.newInstance()
  o.addPoint(-1024, 0.0)
  o.addPoint(ctMin - 10, 0.0)
  o.addPoint(60, 0.02)                      // 胸腔/软组织：透明
  o.addPoint(85, 0.32)                      // 血液：半透明
  o.addPoint(130, 0.72)                     // 心肌：高不透明
  o.addPoint(160, 0.92)
  o.addPoint(2000, 1.0)
  const prop = volumeActor.getProperty()
  prop.setRGBTransferFunction(0, c)
  prop.setScalarOpacity(0, o)
  mainGwin?.getRenderWindow().render()

  // MPR 窗宽窗位（灰阶医学切片，与 ImageMapper 直接兼容）
  for (const key of Object.keys(mprWindows)) {
    const p = mprWindows[key].actor.getProperty()
    p.setColorWindow(ww.value)
    p.setColorLevel(wc.value)
    mprWindows[key].gwin.getRenderWindow().render()
  }
}

function setupMpr(container, key, slicingMode, initSlice) {
  const gwin = vtkGenericRenderWindow.newInstance()
  gwin.setContainer(container)
  gwin.resize()
  const mapper = vtkImageMapper.newInstance()
  mapper.setInputData(imageData)
  mapper.setSlicingMode(slicingMode)
  mapper.setSlice(initSlice)
  const actor = vtkImageSlice.newInstance()
  actor.setMapper(mapper)
  const mprProp = actor.getProperty()
  mprProp.setInterpolationTypeToLinear()
  mprProp.setColorWindow(ww.value)
  mprProp.setColorLevel(wc.value)
  gwin.getRenderer().addActor(actor)
  // 显式相机：视线垂直切片、头侧朝上、平行投影 → 打开即为标准医学方位，无需拖拽
  // 轴位(横断面)：屏幕上=前(胸壁)，屏幕左=患者右侧(镜像，CT 惯例)
  // 冠状(前后位)：头上=+z，患者右侧在屏幕右(非镜像，与3D视图一致)
  // 矢状(侧位)：头上=+z，前方在屏幕左(非镜像，侧面观)
  const cam = gwin.getRenderer().getActiveCamera()
  const d = imageData.getDimensions()
  const cx = d[0] / 2, cy = d[1] / 2, cz = d[2] / 2
  cam.setParallelProjection(true)
  // 平行投影视野高度 = 切片高度的一半×1.15 边距 → 打开即完整显示整幅切片，不放大过头
  const sliceH = key === 'axial' ? d[1] : d[2]
  cam.setParallelScale((sliceH / 2) * 1.15)
  cam.setFocalPoint(cx, cy, cz)
  if (key === 'axial') {              // K 轴位，XY 平面，视线沿 -z（自上而下）
    cam.setPosition(cx, cy, cz + d[2] * 3)
    cam.setViewUp(0, 1, 0)
  } else if (key === 'coronal') {     // J 冠状，XZ 平面，视线沿 -y（自前向后）
    cam.setPosition(cx, cy - d[1] * 3, cz)
    cam.setViewUp(0, 0, 1)
  } else if (key === 'sagittal') {    // I 矢状，YZ 平面，视线沿 +x（自右向左）
    cam.setPosition(cx + d[0] * 3, cy, cz)
    cam.setViewUp(0, 0, 1)
  }
  cam.zoom(1.0)
  gwin.getRenderer().resetCameraClippingRange()
  gwin.getRenderWindow().render()
  mprWindows[key] = { gwin, mapper, actor }
}

// 滑块拖拽节流：input 事件频率高于渲染能力时事件堆积，手感发涩；
// 统一合并到每帧一次，并按变化的那一轴只重绘对应视图（改冠状不该重画轴位/矢状）
let sliceRaf = 0
let slicePending = 'all'
function scheduleSlices(which) {
  slicePending = which || 'all'
  if (sliceRaf) return
  sliceRaf = requestAnimationFrame(() => {
    const w = slicePending
    sliceRaf = 0
    slicePending = 'all'
    updateSlices(w)
  })
}

let cropRaf = 0
function scheduleCrop() {
  if (cropRaf) return
  cropRaf = requestAnimationFrame(() => { cropRaf = 0; applyCrop() })
}

let tfRaf = 0
function scheduleTf() {
  if (tfRaf) return
  tfRaf = requestAnimationFrame(() => { tfRaf = 0; updateTfAndMpr() })
}

function updateSlices(which) {
  if (!mprWindows.axial) return
  const changed = []
  if (!which || which === 'all' || which === 'z') {
    mprWindows.axial.mapper.setSlice(slice.z)
    changed.push('axial')
  }
  if (!which || which === 'all' || which === 'y') {
    mprWindows.coronal.mapper.setSlice(slice.y)
    changed.push('coronal')
  }
  if (!which || which === 'all' || which === 'x') {
    mprWindows.sagittal.mapper.setSlice(slice.x)
    changed.push('sagittal')
  }
  for (const k of changed) mprWindows[k].gwin.getRenderWindow().render()
}

function resetCrop() {
  crop.xMax = dims.value[0]; crop.yMax = dims.value[1]; crop.zMax = dims.value[2]
  applyCrop()
}

function resetCamera() {
  // 重置为标准解剖正面俯视视角（而非 vtk 默认斜视角），标注始终清晰可读
  setAnatomyView()
}

function toggleMeasure() {
  measureMode.value = !measureMode.value
  if (!measureMode.value) clearOverlay()
}

function clearOverlay() {
  for (const key of ['axial', 'coronal', 'sagittal']) {
    const cv = { axial: overlayAxial.value, coronal: overlayCoronal.value, sagittal: overlaySagittal.value }[key]
    if (cv) cv.getContext('2d').clearRect(0, 0, cv.width, cv.height)
    measureTexts[key] = []
    measureLast[key] = null
  }
  measurePts.length = 0
}

function onMprClick(e, key) {
  if (!measureMode.value) return
  const host = { axial: mprAxial.value, coronal: mprCoronal.value, sagittal: mprSagittal.value }[key]
  const rect = host.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  measurePts.push({ x, y, key })
  if (measurePts.length === 2) {
    const [p1, p2] = measurePts
    drawMeasure(p1, p2)
    measurePts.length = 0
  }
}

function drawMeasure(p1, p2) {
  const key = p1.key
  const win = mprWindows[key]
  const cv = { axial: overlayAxial.value, coronal: overlayCoronal.value, sagittal: overlaySagittal.value }[key]
  if (!cv || !win) return
  // 1) 同步叠加层内部像素尺寸到实际显示尺寸（含 dpr），否则绘制坐标被拉伸错位
  const dpr = window.devicePixelRatio || 1
  const cssW = cv.clientWidth || 1
  const cssH = cv.clientHeight || 1
  cv.width = Math.round(cssW * dpr)
  cv.height = Math.round(cssH * dpr)
  const ctx = cv.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)   // 之后一律按 CSS 像素绘制
  // 2) 按当前相机平行投影视野换算真实物理距离：
  //    平行投影下屏幕高度对应 2*parallelScale 个世界单位（数据 spacing=1.0，即 1 世界单位 = 1mm）
  const cam = win.gwin.getRenderer().getActiveCamera()
  const ps = cam.getParallelScale()
  const mmPerPx = (2 * ps) / cssH
  // 不同视角下屏幕两个方向对应的体素轴不同，按 DICOM PixelSpacing 换算真实毫米
  const sp = spacing.value || [1, 1, 1]
  const axisPair = { axial: [sp[0], sp[1]], coronal: [sp[0], sp[2] || 1], sagittal: [sp[1], sp[2] || 1] }[key] || [1, 1]
  const dx = (p2.x - p1.x) * mmPerPx * (axisPair[0] || 1)
  const dy = (p2.y - p1.y) * mmPerPx * (axisPair[1] || 1)
  const dist = Math.sqrt(dx * dx + dy * dy)
  ctx.strokeStyle = '#ffd400'
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(p1.x, p1.y)
  ctx.lineTo(p2.x, p2.y)
  ctx.stroke()
  // 端点小圆点
  ctx.fillStyle = '#ffd400'
  for (const pt of [p1, p2]) {
    ctx.beginPath()
    ctx.arc(pt.x, pt.y, 2.5, 0, Math.PI * 2)
    ctx.fill()
  }
  const label = `${dist.toFixed(1)} mm`
  ctx.font = '12px sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'bottom'
  const midX = (p1.x + p2.x) / 2
  const midY = (p1.y + p2.y) / 2 - 6
  ctx.fillStyle = 'rgba(0,0,0,0.7)'
  const tw = ctx.measureText(label).width
  ctx.fillRect(midX - tw / 2 - 3, midY - 13, tw + 6, 14)
  ctx.fillStyle = '#ffd400'
  ctx.fillText(label, midX, midY)
  measureTexts[key] = label
  // 记录本次测量（尺寸/视口变化时清除，因为缩放后旧线的物理位置已失效）
  measureLast[key] = { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, dist }
}

const STAGE_ORDER = ['volume', 'feature', 'llm', 'verify']

function stageLabel(s) {
  return { idle: '待执行', active: '进行中', done: '完成', fail: '失败' }[s] || ''
}

function applyStages(stage) {
  const idx = STAGE_ORDER.indexOf(stage)
  aiStages.value.forEach((s, i) => {
    if (stage === 'done') { s.state = 'done'; return }
    if (idx < 0) return
    if (i < idx) s.state = 'done'
    else if (i === idx) s.state = 'active'
    else s.state = 'idle'
  })
}

function errText(L, m) {
  if (!L.truth_volume_mm3) return `阈值 ${m.threshold_hu} HU · ${m.thickness} 层`
  const e = ((m.volume_mm3 - L.truth_volume_mm3) / L.truth_volume_mm3) * 100
  return (e >= 0 ? '+' : '') + e.toFixed(2) + '%'
}

function errClass(L, m) {
  if (!L.truth_volume_mm3) return ''
  const e = Math.abs((m.volume_mm3 - L.truth_volume_mm3) / L.truth_volume_mm3 * 100)
  if (e <= 2) return 'ok'
  if (e <= 5) return 'warn'
  return 'bad'
}

// 置信度：调用失败时为 null，不能显示成 0%（0% 会被误读成"模型给了极低置信度"）
function confPct(L) {
  return L.confidence == null ? '0%' : (L.confidence * 100).toFixed(1) + '%'
}

function confText(L) {
  return L.confidence == null ? '未获得' : (L.confidence * 100).toFixed(0) + '%'
}

function rawCallOf(L) {
  const calls = (aiResult.value && aiResult.value.ai_meta && aiResult.value.ai_meta.raw_calls) || []
  return calls.find((c) => c.lesion_index === L.lesion_index) || null
}

function rawPrompt(L) {
  const c = rawCallOf(L)
  const p = (c && c.prompt) || L.raw_prompt || ''
  return p || '（本次未记录到提示词）'
}

function rawAnswer(L) {
  const c = rawCallOf(L)
  const raw = (c && c.raw) || L.raw_output || ''
  if (raw) return raw
  if (L.ai_error) {
    return '（本次调用失败，模型未返回内容）\n\n错误摘要：' + (L.ai_error || '')
      + (L.ai_error_raw ? '\n\n原始报错：' + L.ai_error_raw : '')
  }
  return '（模型返回为空）'
}

function buildReport(res) {
  const c = res.case || {}
  const lines = [
    `患者：${c.patient_name || ''}（${c.gender || ''}，${c.age || ''}岁）  检查号：${c.patient_id || ''}  检查日期：${c.exam_date || ''}`,
    '',
    `检查项目：${c.study_desc || ''}`,
    '',
    '影像所见（由大模型依据实测 HU 特征生成）：',
  ]
  res.lesions.forEach((L, i) => {
    lines.push(`${i + 1}. ${L.name}：${L.finding}`)
  })
  lines.push('', 'AI 结论：')
  res.lesions.forEach((L, i) => {
    lines.push(`${i + 1}. ${L.type}（${L.severity || '—'}），分割阈值 ${L.threshold_hu} HU、层厚 ${L.slice_thickness_mm} mm，置信度 ${L.confidence == null ? '未获得（模型调用失败）' : (L.confidence * 100).toFixed(0) + '%'}。${L.suggest}`)
  })
  lines.push('', `模型：${res.ai_meta.model}，调用 ${res.ai_meta.calls} 次，耗时 ${res.ai_meta.elapsed_sec}s，用量 ${res.ai_meta.usage_total_tokens} tokens。`)
  lines.push('（数字体模仿真数据，仅供科研演示，不具备临床诊断效力）')
  return lines.join('\n')
}

function applyResult(res) {
  aiResult.value = res
  lesions.value = res.lesions || []
  report.value = buildReport(res)
  aiStages.value.forEach((s) => { s.state = 'done' })
  // 模型全部调用失败时，「大模型推理」这一环不能显示成"完成"，否则进度条在骗人
  const meta = res.ai_meta || {}
  if ((meta.failed_calls || 0) > 0 && (meta.failed_calls || 0) >= (meta.calls || 0)) {
    const llm = aiStages.value.find((s) => s.key === 'llm')
    if (llm) llm.state = 'fail'
  }
  progress.value = 100
  progressText.value = '完成'
  updateTfAndMpr()
  markLesions()
  paintModelLesions()
}

/** 打开病例时读取上次已存档的结果，避免每次进页面都重新调用模型 */
async function loadLastResult() {
  try {
    const last = await api.lastAnalyze(props.caseId)
    if (last && last.result) {
      aiSavedAt.value = last.saved_at_text || ''
      applyResult(last.result)
    }
  } catch (e) { /* 该病例还没有存档结果 */ }
}

async function startAnalyze() {
  if (analyzing.value) return
  analyzing.value = true
  aiSavedAt.value = ''
  progress.value = 0
  progressText.value = '提交任务'
  elapsed.value = 0
  aiStages.value.forEach((s) => { s.state = 'idle' })
  const t0 = Date.now()
  const tick = setInterval(() => { elapsed.value = Math.round((Date.now() - t0) / 1000) }, 500)
  try {
    const start = await api.startAnalyze(props.caseId)
    let task = null
    for (let i = 0; i < 220; i++) {
      await new Promise((r) => setTimeout(r, 1200))
      task = await api.getAnalyzeTask(start.task_id)
      progress.value = task.progress || 0
      progressText.value = task.stage_text || ''
      applyStages(task.stage)
      if (task.status === 'done' || task.status === 'error') break
    }
    if (!task || task.status === 'error') throw new Error((task && task.error) || '分析未返回结果')
    applyResult(task.result)
    aiSavedAt.value = '刚刚'
  } catch (err) {
    aiStages.value.forEach((s) => { if (s.state === 'active') s.state = 'fail' })
    alert(err.message || '分析失败')
  } finally {
    clearInterval(tick)
    analyzing.value = false
  }
}

function markLesions() {
  for (const a of lesionActors) {
    mainGwin.getRenderer().removeActor(a)
  }
  lesionActors = []
  const renderer = mainGwin.getRenderer()
  for (const L of lesions.value) {
    const sphere = vtkSphereSource.newInstance()
    sphere.setCenter([L.cx, L.cy, L.cz])
    sphere.setRadius(L.radius + 2)
    const m = vtkMapper.newInstance()
    m.setInputData(sphere.getOutputData())
    const a = vtkActor.newInstance()
    a.setMapper(m)
    a.getProperty().setColor(1, 0.1, 0.1)
    a.getProperty().setOpacity(0.8)
    renderer.addActor(a)
    lesionActors.push(a)
  }
  mainGwin.getRenderWindow().render()
}

function setSliceView(m) {
  sliceView.value = m
  nextTick(() => {
    if (m === '2d') onResize()
  })
}

function onResize() {
  clearOverlay()
  mainGwin?.resize()
  for (const k of Object.keys(mprWindows)) mprWindows[k].gwin.resize()
  resizeModel()
}

onMounted(async () => {
  window.__vtkErrors = []
  window.addEventListener('error', (e) => window.__vtkErrors.push(String(e.message || e)))
  window.addEventListener('unhandledrejection', (e) => window.__vtkErrors.push('REJECT: ' + String(e.reason && e.reason.message || e.reason)))
  // 模型连通性自检（不阻塞页面）
  api.aiHealth().then((h) => {
    aiOnline.value = !!h.ok
    aiModel.value = h.ok ? `已连接 · ${h.model}` : '未连接模型'
  }).catch(() => { aiOnline.value = false })
  try {
    await loadVolume()
  } catch (err) {
    window.__vtkErrors.push('LOAD: ' + err.message)
  }
  // 该病例历史分析结果（已入库的病灶）直接标注，不必重新分析
  try {
    const c = await api.getCase(props.caseId)
    if (c && Array.isArray(c.lesions) && c.lesions.length) {
      lesions.value = c.lesions
      paintModelLesions()
    }
  } catch (e) { /* 无历史病灶时忽略 */ }
  // 该病例上次 AI 分析结果（后端已存档，直接回显，不再重跑模型）
  await loadLastResult()
  window.addEventListener('resize', onResize)
  // 默认以解剖模型展示 3D 视图（体数据渲染仍可一键切回）
  if (renderMode.value === 'model') {
    stopAnnoLoop()
    nextTick(() => { initModelView() })
  }
})

onBeforeUnmount(() => {
  stopAnnoLoop()
  disposeModel()
  if (annoResizeObserver) { annoResizeObserver.disconnect(); annoResizeObserver = null }
  window.removeEventListener('resize', onResize)
  mainGwin?.delete()
  for (const k of Object.keys(mprWindows)) mprWindows[k].gwin.delete()
})
</script>

<style scoped>
.volume-viewer {
  display: flex;
  height: 100%;
  position: relative;
  color-scheme: dark;
  color: #dceefc;
  background: #04090f;
  font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', system-ui, sans-serif;
}

/* ---------- 左栏工具面板 ---------- */
.panel {
  width: 252px;
  box-sizing: border-box;
  flex: none;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 12px;
  background: linear-gradient(180deg, rgba(11, 26, 42, .99) 0%, rgba(7, 15, 25, 1) 62%);
  border-right: 1px solid rgba(60, 170, 240, .12);
  box-shadow: inset -16px 0 28px rgba(2, 7, 13, .5);
}
.panel-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 2px;
  scrollbar-width: thin;
  scrollbar-color: #1b3a52 transparent;
}
.panel-scroll::-webkit-scrollbar, .report::-webkit-scrollbar { width: 6px; }
.panel-scroll::-webkit-scrollbar-track, .report::-webkit-scrollbar-track { background: transparent; }
.panel-scroll::-webkit-scrollbar-thumb, .report::-webkit-scrollbar-thumb { background: #14324a; border-radius: 4px; }
.panel-scroll::-webkit-scrollbar-thumb:hover, .report::-webkit-scrollbar-thumb:hover { background: #275a78; }
.report { scrollbar-width: thin; scrollbar-color: #1b3a52 transparent; }

.panel-title {
  position: relative;
  padding: 0 0 9px 10px;
  margin-bottom: 12px;
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: .14em;
  color: #d4ecfb;
  text-shadow: 0 0 18px rgba(41, 224, 255, .22);
  border-bottom: 1px solid rgba(60, 170, 240, .14);
}
.panel-title::before {
  content: '';
  position: absolute; left: 0; top: 1px; width: 2px; height: 13px;
  background: linear-gradient(180deg, #29e0ff, #7b5cf0);
  box-shadow: 0 0 10px rgba(41, 224, 255, .6);
}
.panel-title::after {
  content: '';
  position: absolute; left: 10px; bottom: -1px; width: 46px; height: 1px;
  background: linear-gradient(90deg, rgba(41, 224, 255, .9), transparent);
}

.group {
  position: relative;
  margin-bottom: 8px;
  padding: 10px 11px 11px;
  /* 长按拖动滑块时会顺带选中标签文字，浏览器随即把它当拖拽源，弹出"松开以搜索" */
  user-select: none;
  -webkit-user-drag: none;
  background: linear-gradient(160deg, rgba(21, 44, 67, .6), rgba(11, 22, 36, .74));
  clip-path: polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%);
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .11);
  transition: box-shadow .18s, filter .18s;
}
.group:hover { filter: brightness(1.05); box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .22); }
.group-title {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 9px;
  font-size: 11px; font-weight: 600; letter-spacing: .1em;
  color: #7fd3f0;
}
.group-title::before {
  content: '';
  width: 4px; height: 4px; flex: none;
  background: #29e0ff;
  transform: rotate(45deg);
  box-shadow: 0 0 8px rgba(41, 224, 255, .7);
}
label {
  display: block;
  margin: 8px 0 4px;
  font-size: 10.5px; letter-spacing: .04em;
  color: #8aa9c3;
}
input[type='range'] {
  width: 100%; margin: 3px 0 5px;
  accent-color: #29e0ff;
  filter: saturate(1.15);
  cursor: pointer;
  -webkit-user-drag: none;
  user-select: none;
  /* 触摸/长按拖动时不要被识别成页面滚动手势，滑块才跟手 */
  touch-action: none;
}

button {
  margin-top: 6px;
  height: 29px;
  max-width: 100%;
  box-sizing: border-box;
  padding: 0 10px;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11.5px;
  letter-spacing: .02em;
  white-space: nowrap;
  color: #c3dcf0;
  background: rgba(24, 48, 70, .85);
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .12);
  transition: background .14s, color .14s, box-shadow .14s;
}
button:hover {
  background: rgba(32, 70, 102, .95);
  color: #eafaff;
  box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .38);
}
button.primary {
  width: 100%;
  font-size: 12px;
  font-weight: 700;
  color: #04121c;
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
  background: linear-gradient(100deg, #24d9f0, #6f8df7);
  box-shadow: 0 0 18px rgba(41, 224, 255, .22);
}
button.primary:hover { filter: brightness(1.12); }
button.primary:disabled {
  background: #1c3145; color: #5f86a3; cursor: not-allowed;
  box-shadow: none; filter: none;
}
button.ghost {
  width: 100%;
  background: transparent;
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .2);
  color: #a6bfd6;
}
button.ghost:hover {
  background: rgba(41, 224, 255, .07);
  box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .5);
  color: #eafaff;
}
button.active {
  background: linear-gradient(100deg, #2ef0ff, #8f8bf8);
  color: #04121c;
  font-weight: 700;
  box-shadow: 0 0 16px rgba(41, 224, 255, .32);
}
button.active:hover { filter: brightness(1.08); }
.presets { display: flex; gap: 5px; margin-top: 8px; }
.presets button { flex: 1; height: 26px; font-size: 10.5px; padding: 0 4px; }

.lesion-item {
  padding: 7px 8px 8px;
  margin-bottom: 6px;
  background: linear-gradient(160deg, rgba(60, 24, 34, .55), rgba(18, 30, 46, .8));
  border-radius: 3px;
  border-left: 2px solid #ff5a5a;
  box-shadow: inset 0 0 0 1px rgba(255, 120, 120, .14);
}
.lesion-name { font-size: 11.5px; font-weight: 700; color: #ffd2d2; }
.src-tag {
  display: inline-block;
  margin-left: 6px;
  padding: 0 5px;
  height: 15px;
  line-height: 15px;
  font-size: 9.5px;
  font-weight: 400;
  border-radius: 3px;
  letter-spacing: .06em;
  vertical-align: 1px;
}
.src-tag.manual { color: #ffd24a; border: 1px solid rgba(255, 210, 74, .38); background: rgba(255, 210, 74, .08); }
.src-tag.auto { color: #29e0ff; border: 1px solid rgba(41, 224, 255, .38); background: rgba(41, 224, 255, .08); }
.lesion-meta { font-size: 10px; color: #93a8c2; margin-top: 3px; line-height: 1.5; }

/* ---------- 影像数据源 ---------- */
.src-row { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
.badge {
  padding: 2px 9px; border-radius: 999px;
  font-family: 'Bahnschrift', 'Consolas', sans-serif;
  font-size: 10px; font-weight: 700; letter-spacing: .1em;
  color: #04121c;
  background: #7f97b3;
}
.badge-dicom { background: linear-gradient(100deg, #45e8c0, #2ee6a8); }
.badge-upload { background: linear-gradient(100deg, #45e8c0, #2ee6a8); }
.badge-npy { background: linear-gradient(100deg, #8fe3ff, #8b9cf6); }
.src-dim {
  font-family: 'Bahnschrift', 'Consolas', sans-serif;
  font-size: 12.5px; font-weight: 600; color: #9fe8ff;
  letter-spacing: .02em;
}
.src-sub { font-size: 10.5px; color: #8aa9c3; line-height: 1.62; }
.src-err { font-size: 10.5px; color: #ff9494; margin-top: 6px; line-height: 1.5; }
.upload-btn {
  display: block;
  margin-top: 6px;
  padding: 6px 10px;
  border-radius: 3px;
  text-align: center;
  font-size: 11.5px;
  color: #a6bfd6;
  background: transparent;
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .2);
  cursor: pointer;
  transition: .15s;
}
.upload-btn:hover { color: #eafaff; background: rgba(41, 224, 255, .07); box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .5); }
.group.dis { opacity: .52; pointer-events: none; filter: saturate(.7); }

/* ---------- 中区 ---------- */
.scene { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main3d {
  flex: 1; min-height: 0; position: relative;
  overflow: hidden;
  background:
    radial-gradient(ellipse 72% 62% at 50% 44%, rgba(26, 92, 138, .34) 0%, rgba(10, 33, 55, .16) 46%, rgba(3, 9, 16, 0) 74%),
    radial-gradient(circle at 50% 46%, #0c2135 0%, #05101c 58%, #01050a 100%);
  box-shadow: inset 0 0 150px 34px rgba(0, 4, 10, .8);
}
.main3d::before {
  content: '';
  position: absolute; inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image:
    linear-gradient(rgba(90, 180, 240, .06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(90, 180, 240, .06) 1px, transparent 1px);
  background-size: 46px 46px, 46px 46px;
  -webkit-mask-image: radial-gradient(ellipse 76% 68% at 50% 46%, rgba(0, 0, 0, .5) 0%, rgba(0, 0, 0, .16) 52%, transparent 76%);
  mask-image: radial-gradient(ellipse 76% 68% at 50% 46%, rgba(0, 0, 0, .5) 0%, rgba(0, 0, 0, .16) 52%, transparent 76%);
}
.main3d::after {
  content: '';
  position: absolute; left: 8%; right: 8%; top: 0; height: 1px;
  pointer-events: none;
  z-index: 0;
  background: linear-gradient(90deg, transparent, rgba(41, 224, 255, .32), transparent);
}
.hud { position: absolute; width: 26px; height: 26px; pointer-events: none; z-index: 2; opacity: 1; }
.hud.tl { left: 10px; top: 10px; border-left: 1px solid rgba(41, 224, 255, .75); border-top: 1px solid rgba(41, 224, 255, .75); }
.hud.tr { right: 10px; top: 10px; border-right: 1px solid rgba(139, 124, 246, .6); border-top: 1px solid rgba(139, 124, 246, .6); }
.hud.bl { left: 10px; bottom: 10px; border-left: 1px solid rgba(139, 124, 246, .45); border-bottom: 1px solid rgba(139, 124, 246, .45); }
.hud.br { right: 10px; bottom: 10px; border-right: 1px solid rgba(41, 224, 255, .45); border-bottom: 1px solid rgba(41, 224, 255, .45); }

.model3d {
  position: absolute; inset: 0;
  z-index: 1;                 /* 必须高于 vtk 画布（static），否则滚轮/拖拽事件被 vtk 抢走 */
  overflow: hidden;
  background:
    radial-gradient(ellipse 58% 18% at 50% 84%, rgba(72, 210, 255, .15) 0%, rgba(40, 140, 210, .05) 46%, rgba(0, 0, 0, 0) 74%),
    radial-gradient(ellipse 66% 58% at 50% 46%, rgba(36, 122, 176, .38) 0%, rgba(13, 42, 68, .16) 48%, rgba(2, 8, 14, 0) 76%),
    radial-gradient(circle at 50% 46%, #0e263b 0%, #06131f 58%, #01050a 100%);
}
.model3d::before {
  content: '';
  position: absolute; inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image:
    linear-gradient(rgba(90, 180, 240, .07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(90, 180, 240, .07) 1px, transparent 1px);
  background-size: 52px 52px, 52px 52px;
  -webkit-mask-image: radial-gradient(ellipse 62% 56% at 50% 46%, rgba(0, 0, 0, .55) 0%, rgba(0, 0, 0, .14) 54%, transparent 78%);
  mask-image: radial-gradient(ellipse 62% 56% at 50% 46%, rgba(0, 0, 0, .55) 0%, rgba(0, 0, 0, .14) 54%, transparent 78%);
}
.model3d canvas { display: block; }
.model-mask {
  position: absolute; inset: 0;
  z-index: 2;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px;
  background: radial-gradient(circle at 50% 44%, #10283f 0%, #071524 60%, #030a12 100%);
}
.mm-title { font-size: 13px; font-weight: 700; letter-spacing: .12em; color: #9fe8ff; }
.mm-bar { width: 280px; height: 6px; background: #12283b; border-radius: 999px; overflow: hidden; box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .16); }
.mm-fill { height: 100%; background: linear-gradient(90deg, #24d9f0, #2ee6a8); transition: width .2s; }
.mm-sub { font-size: 11px; color: #6f93ad; letter-spacing: .06em; }
.model-tip {
  position: absolute; right: 14px; bottom: 12px;
  z-index: 2;
  padding: 4px 10px;
  font-size: 10.5px; color: #8fb8de;
  background: rgba(4, 12, 20, .82);
  border: 1px solid rgba(90, 180, 240, .2);
  border-radius: 999px;
  pointer-events: none;
}
.anno-layer { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }

/* 面板分隔条：拖动可调整左右面板宽度 */
.splitter {
  position: relative;
  width: 7px; flex: none;
  cursor: col-resize;
  background: linear-gradient(90deg, rgba(41, 224, 255, 0), rgba(41, 224, 255, .1), rgba(41, 224, 255, 0));
  transition: background .15s;
}
.splitter:hover { background: linear-gradient(90deg, rgba(41, 224, 255, 0), rgba(41, 224, 255, .42), rgba(41, 224, 255, 0)); }
.splitter::after {
  content: '';
  position: absolute; left: 50%; top: 50%;
  transform: translate(-50%, -50%);
  width: 2px; height: 42px;
  border-radius: 1px;
  background: rgba(41, 224, 255, .38);
}
.splitter:hover::after { background: rgba(41, 224, 255, .9); box-shadow: 0 0 10px rgba(41, 224, 255, .6); }
.anno {
  position: absolute;
  transform: translate(-50%, -108%);
  padding: 2px 8px;
  font-size: 12.5px; font-weight: 600; letter-spacing: .08em;
  color: #ffe08a;
  background: rgba(8, 18, 30, .88);
  border-radius: 3px;
  border: 1px solid rgba(255, 214, 106, .55);
  white-space: nowrap;
}
.anno::after {
  content: '';
  position: absolute; left: 50%; top: 100%;
  transform: translateX(-50%);
  width: 1px; height: 12px;
  background: rgba(255, 214, 106, .7);
}
.anno::before {
  content: '';
  position: absolute; left: 50%; top: calc(100% + 12px);
  transform: translateX(-50%);
  border: 4px solid transparent;
  border-top-color: rgba(255, 214, 106, .85);
}

.mpr-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px;
  background: linear-gradient(180deg, rgba(9, 22, 36, .96), rgba(6, 14, 24, .96));
  border-top: 1px solid rgba(60, 170, 240, .16);
  box-shadow: 0 -10px 26px rgba(2, 6, 12, .5);
}
.mpr-bar-title {
  font-size: 10.5px; font-weight: 600; letter-spacing: .16em;
  color: #8fd0ea;
}
.mpr-bar-hint { font-size: 10px; color: #5d7f99; margin-left: auto; }
.seg {
  display: flex; gap: 3px; padding: 2px;
  border-radius: 999px;
  background: rgba(6, 16, 28, .85);
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .14);
}
.seg button {
  height: 24px; margin: 0; padding: 0 13px;
  font-size: 10.5px; border-radius: 999px;
  background: transparent; color: #9db7cf;
  box-shadow: none;
}
.seg button:hover { background: rgba(41, 224, 255, .1); color: #d8f3ff; box-shadow: none; }
.seg button.active {
  background: linear-gradient(100deg, #2ef0ff, #8f8bf8);
  color: #04121c; font-weight: 700;
  box-shadow: 0 0 14px rgba(41, 224, 255, .28);
}

.mprs {
  height: 210px;
  display: flex; gap: 10px; padding: 8px;
  background: #050d16;
  border-top: 1px solid rgba(60, 170, 240, .12);
  box-shadow: inset 0 10px 22px rgba(2, 7, 13, .6);
}
.slice3d-wrap {
  height: 238px;
  padding: 8px;
  background: #050d16;
  border-top: 1px solid rgba(60, 170, 240, .12);
  box-shadow: inset 0 10px 22px rgba(2, 7, 13, .6);
}
.slice-placeholder { display: flex; align-items: center; justify-content: center; height: 100%; color: #557a94; font-size: 12px; letter-spacing: .06em; }
.mpr-window { flex: 1; position: relative; }
.mpr-title {
  padding: 2px 0;
  font-size: 10.5px; letter-spacing: .1em; color: #7fb8d4; text-align: center;
}
.mpr-canvas { width: 100%; height: calc(100% - 20px); background: #000; }
.overlay { position: absolute; left: 0; top: 20px; width: 100%; height: calc(100% - 20px); pointer-events: none; }

/* ---------- 右栏报告 ---------- */
.report {
  width: 384px;
  box-sizing: border-box;
  flex: none;
  padding: 12px 12px 16px;
  background: linear-gradient(180deg, rgba(11, 26, 42, .99) 0%, rgba(7, 15, 25, 1) 62%);
  border-left: 1px solid rgba(60, 170, 240, .12);
  box-shadow: inset 16px 0 28px rgba(2, 7, 13, .5);
  overflow-y: auto;
}
.empty { padding: 26px 0; text-align: center; font-size: 12px; line-height: 1.85; color: #5d7f99; }
.report-note {
  margin-top: 12px; padding-top: 9px;
  font-size: 10px; line-height: 1.7; color: #d69a1f;
  border-top: 1px dashed rgba(90, 180, 240, .18);
}

.report-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.rp-title { margin-bottom: 0; border-bottom: none; padding-bottom: 0; flex: 1; }
.rp-title::after { display: none; }
.ai-badge {
  padding: 2px 10px; border-radius: 999px;
  font-size: 10px; letter-spacing: .08em; white-space: nowrap;
  color: #7fa0b9;
  background: rgba(20, 40, 58, .7);
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .14);
}
.ai-badge.on {
  color: #8ce6ff;
  background: rgba(41, 224, 255, .1);
  box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .34), 0 0 16px rgba(41, 224, 255, .12);
}

/* ---------- AI 推理流水线 ---------- */
.ai-group {
  position: sticky; top: -1px; z-index: 3;
  flex: none;
  margin: 0 0 12px;
  padding: 11px 12px 12px;
  background: linear-gradient(180deg, #10283c, #0a1725);
  box-shadow:
    inset 0 0 0 1px rgba(41, 224, 255, .2),
    0 12px 24px rgba(2, 6, 12, .7),
    0 0 24px rgba(41, 224, 255, .07);
  clip-path: none;
}
.ai-group::before {
  content: '';
  position: absolute; left: 0; right: 0; top: 0; height: 1px;
  background: linear-gradient(90deg, rgba(41, 224, 255, .75), rgba(139, 124, 246, .45), transparent);
}
.ai-head { display: flex; align-items: center; gap: 7px; }
.ai-dot { width: 6px; height: 6px; border-radius: 50%; background: #3a5872; flex: none; }
.ai-dot.on { background: #29e0ff; box-shadow: 0 0 9px rgba(41, 224, 255, .9); }
.ai-dot.busy { animation: aiPulse 1s infinite; }
.ai-title { font-size: 12px; font-weight: 700; letter-spacing: .08em; color: #cfe9fb; }
.ai-model-line { margin: 5px 0 9px; font-size: 10px; line-height: 1.5; color: #5f86a3; word-break: break-all; }
.ai-stages { display: flex; flex-direction: column; gap: 5px; margin-bottom: 10px; }
.ai-stage { display: flex; align-items: center; gap: 7px; font-size: 11px; color: #7d9ab3; }
.ai-stage-dot { width: 4px; height: 4px; border-radius: 50%; background: #3a5570; flex: none; }
.ai-stage.done { color: #6ff0c2; }
.ai-stage.done .ai-stage-dot { background: #2ee6a8; box-shadow: 0 0 7px rgba(46, 230, 168, .75); }
.ai-stage.active { color: #9fe8ff; font-weight: 600; }
.ai-stage.active .ai-stage-dot { background: #29e0ff; box-shadow: 0 0 9px rgba(41, 224, 255, .85); animation: aiPulse 1s infinite; }
.ai-stage.fail { color: #ff9a9a; }
.ai-stage.fail .ai-stage-dot { background: #ff5a5a; box-shadow: 0 0 7px rgba(255, 90, 90, .7); }
.ai-stage-name { flex: 1; }
.ai-stage-state { font-size: 9px; opacity: .7; letter-spacing: .04em; }
.ai-bar {
  height: 4px; border-radius: 999px; overflow: hidden;
  background: #112a3e;
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .12);
}
.ai-fill {
  height: 100%;
  background: linear-gradient(90deg, #24d9f0, #2ee6a8 55%, #8f8bf8);
  box-shadow: 0 0 12px rgba(41, 224, 255, .5);
  transition: width .4s;
}
.ai-sub { margin-top: 7px; font-size: 10px; line-height: 1.55; color: #8fb8d4; text-align: center; }
.ai-saved { display: block; margin-top: 1px; color: #5f86a3; }
.ai-btn { width: 100%; margin-top: 9px; }
.empty-spin {
  display: inline-block; width: 14px; height: 14px; margin-right: 6px; vertical-align: -3px;
  border: 2px solid rgba(41, 224, 255, .2);
  border-top-color: #29e0ff;
  border-radius: 50%;
  animation: aiSpin .9s linear infinite;
}
@keyframes aiSpin { to { transform: rotate(360deg); } }
@keyframes aiPulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }

/* ---------- 报告卡片 ---------- */
.ai-sum {
  padding: 9px 11px; margin-bottom: 10px;
  background: linear-gradient(160deg, rgba(20, 42, 64, .6), rgba(11, 22, 36, .76));
  clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 0 100%);
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .11);
}
.ai-sum-item { display: flex; justify-content: space-between; gap: 12px; font-size: 10px; line-height: 1.8; }
.ai-sum-item .k { flex: none; color: #5f86a3; letter-spacing: .06em; }
.ai-sum-item .v { max-width: 72%; text-align: right; color: #c3dcf0; word-break: break-all; }

.ai-card {
  position: relative;
  padding: 12px 12px 12px 14px;
  margin-bottom: 10px;
  border-radius: 4px;
  background: linear-gradient(160deg, rgba(22, 46, 70, .7), rgba(12, 26, 42, .84));
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .13);
  transition: box-shadow .18s;
}
.ai-card::before {
  content: '';
  position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
  background: linear-gradient(180deg, #29e0ff, #7b5cf0);
  box-shadow: 0 0 14px rgba(41, 224, 255, .55);
}
.ai-card:hover { box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .34), 0 0 26px rgba(41, 224, 255, .09); }
.ai-card-top { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; }
.ai-idx {
  width: 19px; height: 19px; flex: none;
  display: flex; align-items: center; justify-content: center;
  font-family: 'Bahnschrift', 'Consolas', sans-serif;
  font-size: 11px; font-weight: 700; color: #04121c;
  clip-path: polygon(4px 0, 100% 0, 100% calc(100% - 4px), calc(100% - 4px) 100%, 0 100%, 0 4px);
  background: linear-gradient(140deg, #2ef0ff, #8f8bf8);
}
.ai-name {
  flex: 1; min-width: 0;
  font-size: 12px; font-weight: 700; line-height: 1.45;
  color: #e8f4ff; letter-spacing: .02em;
  word-break: break-word;
}
.ai-tags { display: flex; flex-wrap: wrap; gap: 4px 6px; margin-bottom: 8px; }
.ai-auto-tag {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 2px;
  font-size: 9px; letter-spacing: .03em;
  color: #8ce6ff;
  background: rgba(41, 224, 255, .09);
  border: 1px dashed rgba(41, 224, 255, .45);
}
.ai-low-tag {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 2px;
  font-size: 9px; letter-spacing: .03em;
  color: #ffd08a;
  background: rgba(255, 160, 60, .1);
  border: 1px dashed rgba(255, 160, 60, .45);
}
.ai-fail {
  margin: 0 0 8px; padding: 6px 9px;
  border-radius: 3px;
  background: rgba(255, 118, 88, .1);
  box-shadow: inset 0 0 0 1px rgba(255, 118, 88, .32);
  font-size: 9.5px; line-height: 1.65;
}
.ai-fail-tag { margin-right: 6px; font-weight: 700; letter-spacing: .04em; color: #ff9d7d; }
.ai-fail-text { color: #ffc9b6; word-break: break-word; }
.ai-fallback {
  margin-left: 6px; padding: 1px 6px; border-radius: 2px;
  font-size: 9px; white-space: nowrap;
  color: #ffcf85; background: rgba(255, 196, 107, .12);
}
.empty-hint { display: inline-block; margin-top: 8px; font-size: 10px; line-height: 1.7; color: #5f86a3; }

.ai-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; font-size: 10px; }
.ai-row .k { width: 54px; flex: none; color: #5f86a3; letter-spacing: .04em; }
.ai-row .v { flex: 1; color: #c3dcf0; }
.ai-diff {
  margin-left: 8px; padding: 2px 6px; border-radius: 2px;
  font-size: 9px; white-space: nowrap;
  color: #ffaaaa; background: rgba(255, 90, 90, .12);
}
.ai-conf-bar {
  flex: 1; height: 5px; border-radius: 999px; overflow: hidden;
  background: #112a3e;
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .1);
}
.ai-conf-fill { height: 100%; background: linear-gradient(90deg, #2ee6a8, #7fe3ff); }
.ai-conf-num { width: 34px; text-align: right; font-size: 10px; color: #8fb8d4; }
.ai-conf-num.na { color: #6d8ba5; font-size: 9px; }

.ai-vol { display: flex; gap: 6px; margin: 10px 0 8px; }
.ai-vol-item {
  flex: 1;
  padding: 8px 4px 7px;
  text-align: center;
  clip-path: polygon(0 0, calc(100% - 7px) 0, 100% 7px, 100% 100%, 0 100%);
  background: rgba(8, 20, 32, .8);
  box-shadow: inset 0 0 0 1px rgba(90, 180, 240, .12);
}
.ai-vol-item.hi {
  background: rgba(41, 224, 255, .09);
  box-shadow: inset 0 0 0 1px rgba(41, 224, 255, .5), 0 0 16px rgba(41, 224, 255, .12);
}
.ai-vol-item.truth { background: rgba(12, 24, 38, .7); box-shadow: inset 0 0 0 1px rgba(139, 124, 246, .35); }
.ai-vol-src { font-size: 9px; letter-spacing: .02em; color: #5f86a3; }
.ai-vol-num {
  margin: 4px 0 3px;
  font-family: 'Bahnschrift', 'Consolas', sans-serif;
  font-size: 15px; font-weight: 700; letter-spacing: -.2px;
  color: #eaf6ff; white-space: nowrap;
}
.ai-vol-num span { margin-left: 2px; font-size: 8.5px; font-weight: 400; color: #5f86a3; }
.ai-vol-err { font-size: 9px; font-weight: 600; }
.ai-vol-err.ok { color: #4ef0b0; }
.ai-vol-err.warn { color: #ffc46b; }
.ai-vol-err.bad { color: #ff8a8a; }

.ai-note {
  margin: 9px 0; padding: 7px 9px;
  border-radius: 3px;
  border-left: 2px solid #ffc46b;
  background: rgba(255, 196, 107, .07);
}
.ai-note-line { font-size: 10px; line-height: 1.65; color: #ffd79a; }

.ai-finding { margin-top: 9px; font-size: 11.5px; line-height: 1.75; color: #c8ddf0; }
.ai-suggest {
  margin-top: 7px; padding-top: 7px;
  font-size: 10px; line-height: 1.68; color: #9fd4ef;
  border-top: 1px dashed rgba(90, 180, 240, .18);
}
.ai-raw { margin-top: 9px; }
.ai-raw summary { font-size: 10px; color: #5f86a3; cursor: pointer; letter-spacing: .04em; }
.ai-raw summary:hover { color: #8fd0ea; }
.ai-raw-block { margin-top: 8px; }
.ai-raw-label { margin-bottom: 4px; font-size: 9px; letter-spacing: .04em; color: #5f86a3; }
.ai-raw pre {
  margin-top: 0; padding: 9px;
  max-height: 210px; overflow-y: auto;
  font-size: 10px; line-height: 1.6;
  color: #9db2c9;
  background: #061019;
  border: 1px solid rgba(90, 180, 240, .14);
  border-radius: 3px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
