<template>
  <div class="mslice">
    <div v-for="v in VIEWS" :key="v.key" class="mslice-cell">
      <div class="mslice-head">
        <span class="mslice-label">{{ v.label }}</span>
        <span class="mslice-drag">拖动旋转 / 滚轮缩放 · 双击复位</span>
        <span class="mslice-pos">{{ v.axisText }}{{ slicePos(v) }}</span>
      </div>
      <div :ref="el => setHost(v.key, el)" class="mslice-host"></div>
    </div>

    <div v-if="loading" class="mslice-mask">
      <div class="msk-title">正在加载{{ customModel ? '该病例专属' : '通用解剖' }}三维模型</div>
      <div class="msk-bar"><div class="msk-fill" :style="{ width: pct + '%' }"></div></div>
      <div class="msk-sub">{{ pct }}%</div>
    </div>
    <div v-if="error" class="mslice-err">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

const props = defineProps({
  caseId: { type: [Number, String], required: true },
  volume: { type: Object, default: null },   // { dims:[nx,ny,nz], scalars, heartBBox:{min,max} }
  sliceX: { type: Number, default: 0 },
  sliceY: { type: Number, default: 0 },
  sliceZ: { type: Number, default: 0 },
  lesions: { type: Array, default: () => [] },
})

const VIEWS = [
  { key: 'axial', label: '轴位剖面', axis: 'z', axisText: 'Z=', normal: [0, 0, 1] },
  { key: 'coronal', label: '冠状剖面', axis: 'y', axisText: 'Y=', normal: [0, 1, 0] },
  { key: 'sagittal', label: '矢状剖面', axis: 'x', axisText: 'X=', normal: [1, 0, 0] },
]

// 体素轴 → 模型轴：模型 x↔体素 x、模型 y↔体素 z（层方向）、模型 z↔体素 y
const MODEL_NORMAL = { x: [1, 0, 0], y: [0, 0, 1], z: [0, 1, 0] }

const hosts = {}
const loading = ref(true)
const pct = ref(0)
const error = ref('')
const customModel = ref(false)
let template = null
let ro = null

function setHost(key, el) { if (el) hosts[key] = el }
function slicePos(v) {
  if (v.axis === 'x') return props.sliceX
  if (v.axis === 'y') return props.sliceY
  return props.sliceZ
}

/* ------------------------------------------------ 视图骨架 */
/* 正交视锥：halfBase 是短边半长，长边按 aspect 展开 */
function applyFrustum(view) {
  const host = hosts[view.key]
  if (!host || !view.valid) return
  const w = Math.max(40, host.clientWidth)
  const h = Math.max(40, host.clientHeight)
  const aspect = w / h
  const half = view.halfBase * (aspect >= 1 ? 1 : 1 / aspect)
  view.camera.left = -half * aspect
  view.camera.right = half * aspect
  view.camera.top = half
  view.camera.bottom = -half
  view.camera.updateProjectionMatrix()
}

function fitSize(view) {
  const host = hosts[view.key]
  if (!host || !view.valid) return
  const w = Math.max(40, host.clientWidth)
  const h = Math.max(40, host.clientHeight)
  view.renderer.setPixelRatio(Math.min(1.5, window.devicePixelRatio || 1))
  view.renderer.setSize(w, h, false)
  applyFrustum(view)
  view.renderer.render(view.scene, view.camera)
}

function initView(view) {
  const host = hosts[view.key]
  if (!host || !props.volume) return
  const hb = props.volume.heartBBox
  const ctr = new THREE.Vector3(
    (hb.min[0] + hb.max[0]) / 2,
    (hb.min[1] + hb.max[1]) / 2,
    (hb.min[2] + hb.max[2]) / 2,
  )
  const span = Math.max(hb.max[0] - hb.min[0], hb.max[1] - hb.min[1], hb.max[2] - hb.min[2])

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(1.5, window.devicePixelRatio || 1))
  renderer.localClippingEnabled = true
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.12
  host.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.add(new THREE.HemisphereLight(0xf2f7ff, 0x241a1a, 2.5))
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(1, 1.4, 2.2); scene.add(key)
  const fill = new THREE.DirectionalLight(0xffd5c6, 1.1); fill.position.set(-1.6, 0.6, 1.2); scene.add(fill)
  const rim = new THREE.DirectionalLight(0x9fc6ff, 0.9); rim.position.set(0.3, -1.6, -1.8); scene.add(rim)

  // 模型空间法线（体素轴 → 模型轴映射）
  const mn = new THREE.Vector3(...(MODEL_NORMAL[view.axis] || view.normal))
  view.mnormal = mn

  const plane = new THREE.Plane(mn.clone(), 0)
  view.plane = plane

  // 离屏目标：主相机渲染后读回像素，按心脏主体真实包围盒把三格取景居中（见 bodyProbe）
  view.capRT = new THREE.WebGLRenderTarget(384, 384, { depthBuffer: true })
  view.lesionGroup = new THREE.Group()
  scene.add(view.lesionGroup)

  const half = span * 0.46 + 4
  const cam = new THREE.OrthographicCamera(-half * 1.4, half * 1.4, half, -half, -1000, 1000)
  view.halfBase = half
  view.halfInit = half

  view.renderer = renderer
  view.scene = scene
  view.camera = cam
  view.center = ctr
  view.span = span
  view.valid = true

  // 初始视角：从被剖掉的一侧斜着看进切口（略偏法线，兼顾断面与立体感），剖切面基本正对相机
  view.dist = 900
  view.theta = view.axis === 'x' ? -Math.PI / 2 + 0.34 : (view.axis === 'y' ? Math.PI - 0.34 : 0)
  view.phi = view.axis === 'z' ? -Math.PI / 2 + 0.34 : 0
  placeCamera(view)

  bindDrag(view)
  updateClip(view)
  updateLesions(view)
}

/* 该层切面自身的外接框：取落在切面附近的顶点，得到"这张切面图"的真实范围。
   整体模型包围盒会被远端冠脉树撑大，用它取景心脏就会偏到一边 */
function sliceBounds(view) {
  const mesh = view.bodyMesh
  if (!mesh || !mesh.geometry) return null
  const attr = mesh.geometry.attributes && mesh.geometry.attributes.position
  if (!attr) return null
  const pos = slicePos(view)
  const ax = view.axis === 'z' ? 1 : (view.axis === 'y' ? 2 : 0)
  const key = view.axis + ':' + pos.toFixed(1)
  if (view.sbKey === key && view.sb) return view.sb
  const thick = 6
  const stride = attr.count > 200000 ? 6 : 2
  const t = new THREE.Vector3()
  let x0 = Infinity
  let x1 = -Infinity
  let y0 = Infinity
  let y1 = -Infinity
  let z0 = Infinity
  let z1 = -Infinity
  let sx = 0
  let sy = 0
  let sz = 0
  let n = 0
  for (let i = 0; i < attr.count; i += stride) {
    t.set(attr.getX(i), attr.getY(i), attr.getZ(i)).applyMatrix4(mesh.matrixWorld)
    if (Math.abs(t.getComponent(ax) - pos) > thick) continue
    if (t.x < x0) x0 = t.x
    if (t.x > x1) x1 = t.x
    if (t.y < y0) y0 = t.y
    if (t.y > y1) y1 = t.y
    if (t.z < z0) z0 = t.z
    if (t.z > z1) z1 = t.z
    sx += t.x
    sy += t.y
    sz += t.z
    n++
  }
  if (!isFinite(x0) || !isFinite(y0) || !isFinite(z0)) return null
  const sb = new THREE.Box3(new THREE.Vector3(x0, y0, z0), new THREE.Vector3(x1, y1, z1))
  sb.min.setComponent(ax, pos - thick)
  sb.max.setComponent(ax, pos + thick)
  view.sb = sb
  view.sbKey = key
  // 顶点重心：被大片稀疏血管拉宽的包围盒不能代表切面的视觉重心
  view.sc = n ? new THREE.Vector3(sx / n, sy / n, sz / n) : null
  return sb
}

/* 切开后剩下的那一半才是画面的主体：用它的真实包围盒中心取景，切面实体才会落在格子正中 */
function focusOf(view) {
  sliceBounds(view)
  if (view.sc) return view.sc.clone()
  if (view.bbox) {
    const b = view.bbox
    return new THREE.Vector3(
      (b.min.x + b.max.x) / 2,
      (b.min.y + b.max.y) / 2,
      (b.min.z + b.max.z) / 2,
    )
  }
  return view.center.clone()
}

/* 渲染一次当前画面，只保留"心脏主体"的像素范围。
   伸出去的冠脉枝会把整幅的包围盒撑偏（矢状格右侧空白、轴位格主体被推到边角就是这个原因），
   所以先把 ≤6px 宽的枝状结构用 3 次腐蚀磨掉，再取剩下主块的外接框。 */
const PROBE = 512

function boundsOf(mask, S) {
  let x0 = S
  let x1 = -1
  let y0 = S
  let y1 = -1
  for (let y = 0; y < S; y++) {
    const row = y * S
    for (let x = 0; x < S; x++) {
      if (!mask[row + x]) continue
      if (x < x0) x0 = x
      if (x > x1) x1 = x
      if (y < y0) y0 = y
      if (y > y1) y1 = y
    }
  }
  return x1 < 0 ? null : { x0, x1, y0, y1 }
}

function bodyProbe(view) {
  const S = PROBE
  const rt = view.capRT
  if (!rt || !view.camera || !view.scene) return null
  if (rt.width !== S || rt.height !== S) rt.setSize(S, S)
  const prevClear = view.renderer.getClearColor(new THREE.Color())
  const prevAlpha = view.renderer.getClearAlpha()
  view.renderer.setRenderTarget(rt)
  view.renderer.setClearColor(0x000000, 0)
  view.renderer.clear()
  view.renderer.render(view.scene, view.camera)
  const px = new Uint8Array(S * S * 4)
  view.renderer.readRenderTargetPixels(rt, 0, 0, S, S, px)
  view.renderer.setRenderTarget(null)
  view.renderer.setClearColor(prevClear, prevAlpha)

  let cur = new Uint8Array(S * S)
  for (let i = 0; i < S * S; i++) cur[i] = px[i * 4 + 3] > 40 ? 1 : 0
  let b = boundsOf(cur, S)
  if (!b) return null
  for (let pass = 0; pass < 3; pass++) {
    const nxt = new Uint8Array(S * S)
    for (let y = 1; y < S - 1; y++) {
      const row = y * S
      for (let x = 1; x < S - 1; x++) {
        const i = row + x
        if (cur[i] && cur[i - 1] && cur[i + 1] && cur[i - S] && cur[i + S]) nxt[i] = 1
      }
    }
    cur = nxt
  }
  const e = boundsOf(cur, S)
  // 腐蚀让边缘各收 3px，主块还在就按它换算回真实边缘；磨没了说明主体太细，退回整幅范围
  if (e) {
    b = {
      x0: Math.max(0, e.x0 - 3),
      x1: Math.min(S - 1, e.x1 + 3),
      y0: Math.max(0, e.y0 - 3),
      y1: Math.min(S - 1, e.y1 + 3),
    }
  }
  return {
    x0: b.x0, x1: b.x1, y0: b.y0, y1: b.y1,
    cx: (b.x0 + b.x1) / 2,
    cy: (b.y0 + b.y1) / 2,
    w: b.x1 - b.x0 + 1,
    h: b.y1 - b.y0 + 1,
  }
}

/* 让主体落在格子正中：zoom=true 时同时等比铺满（切面变化用），false 只回中（手势松手用） */
function bodyFit(view, zoom) {
  const S = PROBE
  let p = bodyProbe(view)
  if (!p) return
  if (zoom) {
    // 主体被裁到画幅外时先退一步看全，否则量到的不是它的真实尺寸
    for (let i = 0; i < 2 && (p.x0 <= 1 || p.x1 >= S - 2 || p.y0 <= 1 || p.y1 >= S - 2); i++) {
      view.halfBase = Math.max(6, Math.min(600, view.halfBase * 1.35))
      applyFrustum(view)
      p = bodyProbe(view) || p
    }
    const fw = view.camera.right - view.camera.left
    const fh = view.camera.top - view.camera.bottom
    if (!(fw > 0) || !(fh > 0)) return
    const wW = (p.w / S) * fw
    const wH = (p.h / S) * fh
    let k = Math.min(fw / wW, fh / wH) * 0.84
    if (!isFinite(k) || k <= 0) return
    k = Math.max(0.5, Math.min(3, k))
    if (Math.abs(k - 1) > 0.01) {
      view.halfBase = Math.max(6, Math.min(600, view.halfBase / k))
      applyFrustum(view)
      p = bodyProbe(view) || p
    }
  }
  // readRenderTargetPixels 行序自底向上，与 NDC 的 y 方向一致，不再翻转
  const nx = p.cx / (S / 2) - 1
  const ny = p.cy / (S / 2) - 1
  if (!isFinite(nx) || !isFinite(ny)) return
  if (Math.abs(nx) < 0.002 && Math.abs(ny) < 0.002) return
  const halfW = (view.camera.right - view.camera.left) / 2
  const halfH = (view.camera.top - view.camera.bottom) / 2
  const vbRight = new THREE.Vector3().setFromMatrixColumn(view.camera.matrixWorld, 0)
  const vbUp = new THREE.Vector3().setFromMatrixColumn(view.camera.matrixWorld, 1)
  view.camera.position.addScaledVector(vbRight, nx * halfW).addScaledVector(vbUp, ny * halfH)
  view.camera.updateMatrixWorld(true)
  view.camera.matrixWorldInverse.copy(view.camera.matrixWorld).invert()
}

/* 球坐标定位相机：theta 绕 y 轴，phi 俯仰，正交视锥不随距离变化
   斜着看时几何中心不等于屏幕中心，再按"剩余半体的包围盒投影"补一次屏幕居中 */
function placeCamera(view, precise) {
  const cp = Math.cos(view.phi), sp = Math.sin(view.phi)
  const st = Math.sin(view.theta), ct = Math.cos(view.theta)
  const dx = cp * st, dy = sp, dz = cp * ct
  const c = focusOf(view)
  view.camera.position.set(c.x + dx * view.dist, c.y + dy * view.dist, c.z + dz * view.dist)
  const ax = Math.abs(dx), ay = Math.abs(dy), az = Math.abs(dz)
  if (ay >= ax && ay >= az) view.camera.up.set(0, 0, 1)
  else view.camera.up.set(0, 1, 0)
  view.camera.lookAt(c)
  view.camera.updateProjectionMatrix()
  view.camera.updateMatrixWorld(true)
  view.camera.matrixWorldInverse.copy(view.camera.matrixWorld).invert()

  const b = sliceBounds(view) || view.bbox
  if (!b) return
  const pAxis = view.axis === 'z' ? 'y' : (view.axis === 'y' ? 'z' : 'x')
  const pos = slicePos(view)
  let nx0 = 2
  let nx1 = -2
  let ny0 = 2
  let ny1 = -2
  const p0 = new THREE.Vector3()
  for (let i = 0; i < 8; i++) {
    p0.set(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z)
    if (pAxis === 'x') p0.x = Math.max(p0.x, pos)
    else if (pAxis === 'y') p0.y = Math.max(p0.y, pos)
    else p0.z = Math.max(p0.z, pos)
    p0.project(view.camera)
    if (p0.x < nx0) nx0 = p0.x
    if (p0.x > nx1) nx1 = p0.x
    if (p0.y < ny0) ny0 = p0.y
    if (p0.y > ny1) ny1 = p0.y
  }
  const nx = (nx0 + nx1) / 2
  const ny = (ny0 + ny1) / 2
  if (!isFinite(nx) || !isFinite(ny)) return
  const halfW = (view.camera.right - view.camera.left) / 2
  const halfH = (view.camera.top - view.camera.bottom) / 2
  const vbRight = new THREE.Vector3().setFromMatrixColumn(view.camera.matrixWorld, 0)
  const vbUp = new THREE.Vector3().setFromMatrixColumn(view.camera.matrixWorld, 1)
  view.camera.position.addScaledVector(vbRight, nx * halfW)
  view.camera.position.addScaledVector(vbUp, ny * halfH)
  view.camera.updateMatrixWorld(true)
  view.camera.matrixWorldInverse.copy(view.camera.matrixWorld).invert()

  // 切面变化 / 手势结束时按"心脏主体"的真实像素范围收一次：既居中，也等比铺满格子
  if (precise) bodyFit(view, true)
}

function bindDrag(view) {
  const el = view.renderer.domElement
  let dragging = false
  let lx = 0
  let ly = 0
  el.style.cursor = 'grab'
  el.style.touchAction = 'none'
  el.addEventListener('pointerdown', e => {
    dragging = true
    lx = e.clientX
    ly = e.clientY
    el.style.cursor = 'grabbing'
    try { el.setPointerCapture(e.pointerId) } catch (x) { /* ignore */ }
  })
  el.addEventListener('pointermove', e => {
    if (!dragging) return
    const dx = e.clientX - lx
    const dy = e.clientY - ly
    lx = e.clientX
    ly = e.clientY
    view.theta -= dx * 0.008
    view.phi = Math.max(-1.45, Math.min(1.45, view.phi + dy * 0.008))
    placeCamera(view)
    view.renderer.render(view.scene, view.camera)
  })
  const stop = e => {
    if (!dragging) return
    dragging = false
    el.style.cursor = 'grab'
    try { el.releasePointerCapture(e.pointerId) } catch (x) { /* ignore */ }
    // 松手时把主体重新摆回格子正中，只修取景、不改用户转到的角度，也不动用户缩放的尺度
    placeCamera(view, false)
    bodyFit(view, false)
    view.renderer.render(view.scene, view.camera)
  }
  el.addEventListener('pointerup', stop)
  el.addEventListener('pointercancel', stop)
  el.addEventListener('wheel', e => {
    e.preventDefault()
    const k = e.deltaY > 0 ? 1.12 : 1 / 1.12
    view.halfBase = Math.max(6, Math.min(600, view.halfBase * k))
    fitSize(view)
  }, { passive: false })
  el.addEventListener('dblclick', () => {
    view.theta = view.axis === 'x' ? -Math.PI / 2 : (view.axis === 'y' ? Math.PI : 0)
    view.phi = view.axis === 'z' ? -Math.PI / 2 : 0
    view.halfBase = view.halfInit
    placeCamera(view, true)
    fitSize(view)
  })
}

function updateClip(view) {
  // 平面保留法线正侧：轴位保留 z>=pos、冠状 y>=pos、矢状 x>=pos
  view.plane.constant = -slicePos(view)
  // 三格只显示模型被刀切开的实体，不再叠断面贴片
  // 切面移动后剩下的半体变了，取景中心跟着走，实体始终居中
  placeCamera(view, true)
}

function updateLesions(view) {
  // 三格只表现模型剖切本身，病灶标记统一放在主 3D 视图上，避免两处位置口径不一致
  if (!view.lesionGroup) return
  while (view.lesionGroup.children.length) {
    const c = view.lesionGroup.children.pop()
    c.geometry.dispose(); c.material.dispose()
  }
}

/* ------------------------------------------------ 模型：病例专属优先 */
async function pickModelUrl() {
  const names = [`/models/case_${String(props.caseId).padStart(2, '0')}.glb`, `/models/case_${props.caseId}.glb`]
  for (const n of names) {
    try {
      const r = await fetch(n, { method: 'HEAD' })
      const ct = (r.headers.get('content-type') || '').toLowerCase()
      if (r.ok && !ct.includes('text/html')) return { url: n, custom: true }
    } catch (e) { /* 继续 */ }
  }
  return { url: '/models/heart_model.glb', custom: false }
}

/* 取面数最多的 mesh（心脏主体），用于生成内芯 */
function pickBiggest(root) {
  let best = null
  let bestCount = 0
  root.traverse(o => {
    if (!o.isMesh || !o.geometry) return
    const g = o.geometry
    const n = (g.index ? g.index.count : (g.attributes.position ? g.attributes.position.count : 0)) || 0
    if (n > bestCount) { bestCount = n; best = o }
  })
  return best
}

function attachModel(view, scale, center) {
  // 外壳
  const shell = template.clone(true)
  shell.traverse(o => {
    if (!o.isMesh) return
    o.material = o.material.clone()
    o.material.clippingPlanes = [view.plane]
    o.material.clipIntersection = false
    o.material.side = THREE.DoubleSide
    o.material.metalness = 0.08
    o.material.roughness = 0.62
    o.material.polygonOffset = true
    o.material.polygonOffsetFactor = 1
    o.material.polygonOffsetUnits = 1
    // 非水密模型切开后内壁法线反向会发黑，用暗红自发光模拟心肌断面
    o.material.emissive = new THREE.Color(0x5c1d16)
    o.material.emissiveIntensity = 0.92
    if (o.material.map) o.material.map.colorSpace = THREE.SRGBColorSpace
    o.material.needsUpdate = true
  })
  shell.scale.setScalar(scale)
  shell.position.copy(center)
  view.scene.add(shell)
  view.shell = shell
  shell.updateWorldMatrix(true, true)
  // 取景用心脏主体的包围盒：包含冠脉树会把中心带偏
  const body = pickBiggest(shell)
  view.bodyMesh = body || shell
  view.bbox = new THREE.Box3().setFromObject(body || shell)

  // 内芯：只取心脏主体，缩到 0.72 且不参与裁剪，剖开时把切口填成实体（心肌色）
  const big = pickBiggest(shell)
  if (big) {
    big.updateWorldMatrix(true, false)
    const geoBox = new THREE.Box3().setFromBufferAttribute(big.geometry.attributes.position)
    const gc = geoBox.getCenter(new THREE.Vector3())
    const m = big.matrixWorld.clone()
    m.multiply(new THREE.Matrix4().makeTranslation(gc.x, gc.y, gc.z))
    m.multiply(new THREE.Matrix4().makeScale(0.72, 0.72, 0.72))
    m.multiply(new THREE.Matrix4().makeTranslation(-gc.x, -gc.y, -gc.z))
    const core = new THREE.Mesh(big.geometry, new THREE.MeshStandardMaterial({
      color: 0x53201b, roughness: 1.0, metalness: 0.0, side: THREE.DoubleSide,
      emissive: new THREE.Color(0x150404), emissiveIntensity: 0.5,
    }))
    core.matrixAutoUpdate = false
    core.matrix.copy(m)
    core.renderOrder = 1
    // 切面已用 CT 断面贴片（cap）填充，内芯会挡在贴片前面、让剖面发黑，故不显示
    core.visible = false
    view.scene.add(core)
    view.core = core
  }
  view.big = big
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { url, custom } = await pickModelUrl()
    customModel.value = custom
    const gltf = await new Promise((ok, bad) => {
      new GLTFLoader().load(url, ok,
        (e) => { if (e && e.total) pct.value = Math.min(90, Math.round((e.loaded / e.total) * 90)) }, bad)
    })
    pct.value = 92
    const root = gltf.scene
    root.traverse(o => {
      if (!o.isMesh) return
      const mats = Array.isArray(o.material) ? o.material : [o.material]
      mats.forEach(m => { if (m) { m.metalness = 0.08; m.roughness = 0.62; m.needsUpdate = true } })
    })
    const box = new THREE.Box3().setFromObject(root)
    const size = box.getSize(new THREE.Vector3())
    const center = box.getCenter(new THREE.Vector3())
    root.position.set(-center.x, -center.y, -center.z)
    template = new THREE.Group()
    template.add(root)

    const hb = props.volume.heartBBox
    const volSpan = Math.max(hb.max[0] - hb.min[0], hb.max[1] - hb.min[1], hb.max[2] - hb.min[2])
    const glbSpan = Math.max(size.x, size.y, size.z) || 1
    const s = volSpan / glbSpan
    const ctr = new THREE.Vector3(
      (hb.min[0] + hb.max[0]) / 2,
      (hb.min[1] + hb.max[1]) / 2,
      (hb.min[2] + hb.max[2]) / 2,
    )

    pct.value = 100
    await nextTick()
    VIEWS.forEach(v => {
      initView(v)
      attachModel(v, s, ctr)
      updateClip(v)
      // 视锥按 host 真实宽高定好之后再收一次取景，否则铺满比例会按错误的画幅算
      fitSize(v)
      bodyFit(v, true)
      v.renderer.render(v.scene, v.camera)
    })
    loading.value = false
  } catch (e) {
    error.value = '模型加载失败：' + (e.message || e)
    loading.value = false
  }
}

onMounted(() => {
  if (!props.volume) { loading.value = false; return }
  load()
  ro = new ResizeObserver(() => {
    VIEWS.forEach(v => { if (v.valid) fitSize(v) })
  })
  Object.values(hosts).forEach(h => h && ro.observe(h))
})

onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  VIEWS.forEach(v => {
    if (!v.valid) return
    v.renderer.dispose()
    if (v.renderer.domElement.parentNode) v.renderer.domElement.parentNode.removeChild(v.renderer.domElement)
    v.valid = false
  })
})

/* 切面重算较重（渲染 + 读回 + 填洞），用一帧合并多次滑块事件，避免拖滑块卡顿 */
let capRaf = 0
const capQueue = new Set()
function scheduleCap(view) {
  if (!view.valid) return
  capQueue.add(view)
  if (capRaf) return
  capRaf = requestAnimationFrame(() => {
    capRaf = 0
    const list = Array.from(capQueue)
    capQueue.clear()
    list.forEach(v => { if (v.valid) v.renderer.render(v.scene, v.camera) })
  })
}

watch(() => [props.sliceX, props.sliceY, props.sliceZ], () => {
  VIEWS.forEach(v => {
    if (!v.valid) return
    updateClip(v)
    scheduleCap(v)
  })
})
watch(() => props.lesions, () => {
  VIEWS.forEach(v => { if (v.valid) { updateLesions(v); v.renderer.render(v.scene, v.camera) } })
}, { deep: true })
</script>

<style scoped>
.mslice { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; height: 100%; position: relative; }
.mslice-cell {
  display: flex; flex-direction: column; overflow: hidden; border-radius: 10px;
  background: radial-gradient(circle at 50% 42%, #16202c 0%, #0b1119 78%);
  border: 1px solid #1d2836;
}
.mslice-head { display: flex; align-items: baseline; gap: 8px; padding: 6px 9px 1px; font-size: 12px; }
.mslice-label { color: #cfe3f5; letter-spacing: 0.4px; }
.mslice-drag { font-size: 10px; color: #4d6c86; }
.mslice-pos { margin-left: auto; color: #6f8aa3; font-variant-numeric: tabular-nums; }
.mslice-host { flex: 1; min-height: 0; }
.mslice-host :deep(canvas) { display: block; width: 100%; height: 100%; }
.mslice-mask {
  position: absolute; inset: 0; display: flex; flex-direction: column; gap: 8px;
  align-items: center; justify-content: center;
  background: rgba(9, 14, 20, 0.9); color: #9dc0dd; font-size: 13px; z-index: 5;
}
.msk-title { color: #cfe3f5; }
.msk-bar { width: 220px; height: 6px; border-radius: 3px; background: #1b2a3a; overflow: hidden; }
.msk-fill { height: 100%; background: linear-gradient(90deg, #2f7fb8, #71c1f0); transition: width 0.2s; }
.msk-sub { color: #6f8aa3; font-size: 12px; }
.mslice-err { position: absolute; left: 10px; top: 10px; color: #ff8a80; font-size: 12px; }
</style>
