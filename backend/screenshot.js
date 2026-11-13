// CDP 截图：导航到 auto_login（登录后跳 viewer/1），等待 3D 渲染，截图
const http = require('http');
const fs = require('fs');
const EDGE_DEBUG = 'http://127.0.0.1:9222';
const URL = 'http://localhost:5173/auto_login.html';
const WAIT_MS = Number(process.argv[2] || 90000);
const OUT = process.argv[3] || 'edge_check.png';

function getJson(path) {
  return new Promise((res, rej) => {
    http.get(EDGE_DEBUG + path, r => {
      let d = '';
      r.on('data', c => d += c);
      r.on('end', () => res(JSON.parse(d)));
    }).on('error', rej);
  });
}
function sendWs(wsUrl, msg) {
  return new Promise((res, rej) => {
    const ws = new WebSocket(wsUrl);
    ws.onopen = () => ws.send(JSON.stringify(msg));
    ws.onmessage = e => { ws.close(); res(JSON.parse(e.data)); };
    ws.onerror = rej;
  });
}

(async () => {
  const targets = await getJson('/json');
  const page = targets.find(t => t.type === 'page');
  if (!page) throw new Error('no page target');
  const wsUrl = page.webSocketDebuggerUrl;
  await sendWs(wsUrl, { id: 1, method: 'Page.enable' });
  await sendWs(wsUrl, { id: 2, method: 'Runtime.enable' });
  await sendWs(wsUrl, { id: 3, method: 'Page.navigate', params: { url: URL } });
  await new Promise(r => setTimeout(r, 3000));
  // 等 3D 渲染完成：轮询 canvas 是否非全黑
  const started = Date.now();
  let ready = false;
  while (Date.now() - started < WAIT_MS) {
    const ev = await sendWs(wsUrl, {
      id: 10, method: 'Runtime.evaluate',
      params: { expression: `(() => { const c = document.querySelector('canvas'); if (!c) return 'no-canvas'; const g = c.getContext('2d'); if (!g) return 'no-ctx'; const d = g.getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < d.length; i += 4) { if (d[i] + d[i+1] + d[i+2] > 60) n++; } return 'px=' + n; })()`, returnByValue: true }
    });
    const val = ev.result && ev.result.result && ev.result.result.value;
    if (val && val.startsWith('px=') && parseInt(val.slice(3)) > 2000) { ready = true; break; }
    await new Promise(r => setTimeout(r, 5000));
  }
  console.log('render-ready:', ready);
  const shot = await sendWs(wsUrl, { id: 20, method: 'Page.captureScreenshot', params: { format: 'png' } });
  fs.writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
  console.log('saved:', OUT);
  process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
