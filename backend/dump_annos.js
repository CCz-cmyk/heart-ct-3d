// CDP：读 .anno 标注 DOM 位置（left/top），对比标注是否贴合3D结构
const http = require('http');
const fs = require('fs');
const EDGE_DEBUG = 'http://127.0.0.1:9222';
const WAIT_MS = Number(process.argv[2] || 200000);

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
  const wsUrl = page.webSocketDebuggerUrl;
  await sendWs(wsUrl, { id: 1, method: 'Page.enable' });
  await sendWs(wsUrl, { id: 2, method: 'Runtime.enable' });
  await sendWs(wsUrl, { id: 3, method: 'Page.navigate', params: { url: 'http://localhost:5173/auto_login.html' } });
  await new Promise(r => setTimeout(r, WAIT_MS));
  const ev = await sendWs(wsUrl, {
    id: 10, method: 'Runtime.evaluate',
    params: { expression: `(() => {
      const out = [];
      document.querySelectorAll('.anno').forEach(el => {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        out.push({ text: el.textContent.trim(), left: Math.round(r.left), top: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), bg: st.backgroundColor });
      });
      const c = document.querySelector('canvas');
      let px = -1;
      if (c) { const g = c.getContext('2d'); if (g) { const d = g.getImageData(0,0,c.width,c.height).data; let n=0; for (let i=0;i<d.length;i+=4){ if(d[i]+d[i+1]+d[i+2]>60) n++; } px = n; } }
      return JSON.stringify({ annos: out, canvasPx: px, canvas: c ? [c.width, c.height] : null });
    })()`, returnByValue: true }
  });
  const val = JSON.parse(ev.result.result.value);
  console.log(JSON.stringify(val, null, 1));
  const shot = await sendWs(wsUrl, { id: 20, method: 'Page.captureScreenshot', params: { format: 'png' } });
  fs.writeFileSync('C:\\heart_3d_project\\data\\preview\\anno_check.png', Buffer.from(shot.result.data, 'base64'));
  console.log('saved anno_check.png');
  process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
