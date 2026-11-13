const BASE = '/api'

function token() {
  return localStorage.getItem('token') || ''
}

async function req(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }
  // 传 undefined 表示删掉该头（FormData 时由浏览器自动生成 multipart boundary）
  Object.keys(headers).forEach((k) => {
    if (headers[k] === undefined || headers[k] === null) delete headers[k]
  })
  if (token()) headers['Authorization'] = 'Bearer ' + token()
  const resp = await fetch(BASE + path, { ...options, headers })
  if (resp.status === 401) {
    localStorage.removeItem('token')
    location.href = '/login'
    throw new Error('登录失效')
  }
  if (!resp.ok) {
    let msg = `请求失败(${resp.status})`
    try { msg = (await resp.json()).detail || msg } catch (e) { /* ignore */ }
    throw new Error(msg)
  }
  return resp
}

export const api = {
  async login(username, password) {
    const r = await req('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    return r.json()
  },
  async listCases() {
    const r = await req('/cases')
    return r.json()
  },
  /** 上传 DICOM zip 新建病例档案：解包 → 解析 → 生成体数据 → 入库 */
  async createCase(formData) {
    const r = await req('/cases', {
      method: 'POST',
      body: formData,
      headers: { 'Content-Type': undefined },
    })
    return r.json()
  },
  /** 修改档案患者信息（影像数据不变） */
  async updateCase(id, data) {
    const r = await req(`/cases/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
    return r.json()
  },
  /** 删除档案（连带病灶、体数据与 DICOM 文件） */
  async deleteCase(id) {
    const r = await req(`/cases/${id}`, { method: 'DELETE' })
    return r.json()
  },
  async getCase(id) {
    const r = await req(`/cases/${id}`)
    return r.json()
  },
  /**
   * 读取体数据。source: auto（默认，有 DICOM 用 DICOM）/ npy / dicom / upload
   * 返回 dims（X,Y,Z 顺序）、spacing（mm）、来源标记与切片数
   */
  async getVolume(id, source = 'auto') {
    const r = await req(`/cases/${id}/volume?source=${source}`, {
      headers: { 'Content-Type': undefined },
    })
    const dims = (r.headers.get('X-Vol-Dims') || '128,128,96').split(',').map(Number)
    const spacing = (r.headers.get('X-Vol-Spacing') || '1,1,1').split(',').map(Number)
    const buf = await r.arrayBuffer()
    return {
      dims,
      spacing,
      buffer: buf,
      source: r.headers.get('X-Vol-Source') || 'npy',
      slices: Number(r.headers.get('X-Vol-Slices') || dims[2]),
    }
  },
  async getDicomInfo(id, source = 'dicom') {
    const r = await req(`/cases/${id}/dicom/info?source=${source}`)
    return r.json()
  },
  /** 上传 zip 形式的 DICOM 序列，导入后可作为 source=upload 渲染 */
  async uploadDicom(id, file) {
    const fd = new FormData()
    fd.append('file', file)
    const r = await req(`/cases/${id}/dicom/upload`, {
      method: 'POST',
      body: fd,
      headers: { 'Content-Type': undefined },
    })
    return r.json()
  },
  /** 下载整套 DICOM 序列（zip），可直接用 RadiAnt / 3D Slicer 打开 */
  async downloadDicomZip(id, source = 'dicom') {
    const r = await req(`/cases/${id}/dicom/series.zip?source=${source}`, {
      headers: { 'Content-Type': undefined },
    })
    return r.blob()
  },
  /** 启动 AI 分析（异步任务），返回 task_id */
  async startAnalyze(id) {
    const r = await req(`/cases/${id}/analyze`, { method: 'POST' })
    return r.json()
  },
  /** 轮询 AI 分析任务：stage / progress / stage_text / status / result */
  async getAnalyzeTask(tid) {
    const r = await req(`/analyze/tasks/${tid}`)
    return r.json()
  },
  /** 模型配置自检（不返回密钥） */
  async aiHealth() {
    const r = await req('/analyze/health')
    return r.json()
  },
  /** 该病例最近一次已保存的分析结果（不发模型请求） */
  async lastAnalyze(id) {
    const r = await req(`/cases/${id}/analyze/latest`)
    return r.json()
  },
  /** 该病例专属三维网格（GLB，由病例体数据等值面重建），返回 ArrayBuffer */
  async getCaseMesh(id, source = 'auto') {
    const r = await req(`/cases/${id}/mesh?source=${source}`, {
      headers: { 'Content-Type': undefined },
    })
    return {
      buffer: await r.arrayBuffer(),
      faces: Number(r.headers.get('X-Mesh-Faces') || 0),
      dims: (r.headers.get('X-Mesh-Dims') || '').split(',').map(Number),
    }
  },
  async getCaseMeshMeta(id, source = 'auto') {
    const r = await req(`/cases/${id}/mesh/meta?source=${source}`)
    return r.json()
  },
}
