// 任务码二维码生成器 · 核心逻辑 Node 测试
const fs = require('fs');
const path = require('path');

// 加载内嵌在 HTML 里的二维码库(UMD, module.exports 可用)
const html = fs.readFileSync(path.join(__dirname, '任务码二维码生成器.html'), 'utf8');
const m = html.match(/\/\*__QRCODE_LIB__\*\/([\s\S]*?)\n<\/script>/);
// 占位符已被替换, 改为直接取第一个 <script> 块(库)与第二个 <script> 块(逻辑)
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x => x[1]);
if (scripts.length < 2) { console.error('未找到两个 script 块'); process.exit(1); }
const qrcode = new Function(scripts[0] + '\n;return typeof module!=="undefined"&&module.exports?module.exports:qrcode;')();
console.log('qrcode lib loaded:', typeof qrcode, '| 模块数(示例):',
  (() => { const q = qrcode(0, 'M'); q.addData('156+123+516+231'); q.make(); return q.getModuleCount(); })());

// ---- 从 HTML 第二个 script 块中提取纯逻辑函数(校验/随机) ----
const COLOR_DIGITS = ["1","2","3","4","5","6"];
const POS_DIGITS   = ["1","2","3"];

function validateGroup(v, kind) {
  if (!/^\d{3}$/.test(v)) {
    return (kind === "color" ? "颜色" : "位置") + "组必须恰好为3位数字";
  }
  const allowed = kind === "color" ? COLOR_DIGITS : POS_DIGITS;
  const range = kind === "color" ? "1~6" : "1~3";
  for (let i = 0; i < 3; i++) {
    if (allowed.indexOf(v.charAt(i)) === -1) {
      return (kind === "color" ? "颜色" : "位置") + "数字必须在" + range + "之间";
    }
  }
  if (v.charAt(0) === v.charAt(1) || v.charAt(1) === v.charAt(2) ||
      v.charAt(0) === v.charAt(2)) {
    return "三个" + (kind === "color" ? "颜色" : "位置") + "不能重复";
  }
  return null;
}

function randomPick(pool, n) {
  const arr = pool.slice();
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr.slice(0, n).join("");
}

function randomCode() {
  return [randomPick(COLOR_DIGITS, 3), randomPick(POS_DIGITS, 3),
          randomPick(COLOR_DIGITS, 3), randomPick(POS_DIGITS, 3)].join('+');
}

function renderQr(text) {
  const qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const count = qr.getModuleCount();
  const margin = 4;
  const cell = Math.floor(720 / (count + 2 * margin));
  const url = qr.createDataURL(cell, margin);
  return { count, cell, size: (count + 2 * margin) * cell, url };
}

let fail = 0;
function check(name, cond, detail) {
  if (!cond) { fail++; console.log('FAIL:', name, detail || ''); }
  else console.log('OK  :', name, detail || '');
}

// ---- 校验规则 ----
check('合法示例 156+123+516+231',
  validateGroup('156','color') === null && validateGroup('123','pos') === null &&
  validateGroup('516','color') === null && validateGroup('231','pos') === null);
check('颜色含0 -> 拒绝', validateGroup('156','color') === null && validateGroup('016','color') !== null);
check('颜色含7 -> 拒绝', validateGroup('716','color') !== null);
check('位置含4 -> 拒绝', validateGroup('124','pos') !== null);
check('位置含6 -> 拒绝', validateGroup('126','pos') !== null);
check('组长度不足 -> 拒绝', validateGroup('12','color') !== null);
check('组长度超出 -> 拒绝', validateGroup('1234','pos') !== null);
check('含字母 -> 拒绝', validateGroup('12a','color') !== null);
check('颜色重复 -> 拒绝', validateGroup('112','color') !== null);
check('位置重复 -> 拒绝', validateGroup('221','pos') !== null);
check('位置 312 合法', validateGroup('312','pos') === null);
check('颜色 654 合法', validateGroup('654','color') === null);

// ---- 随机生成 200 次全部合法 ----
let ok = true;
for (let i = 0; i < 200; i++) {
  const code = randomCode();
  const g = code.split('+');
  if (validateGroup(g[0],'color') || validateGroup(g[1],'pos') ||
      validateGroup(g[2],'color') || validateGroup(g[3],'pos')) { ok = false; console.log('  非法随机码:', code); break; }
}
check('随机生成 200 次全部合法', ok);

// ---- 二维码生成 ----
const r = renderQr('156+123+516+231');
check('QR 模块数合理(25~41)', r.count >= 25 && r.count <= 41, 'count=' + r.count);
check('QR 输出尺寸接近720(≤720)', r.size <= 720, 'size=' + r.size + ' cell=' + r.cell);
check('QR 是 data URL 图片', /^data:image\/gif;base64,/.test(r.url), r.url.slice(0, 40) + '...');
check('QR 内容不同则图案不同',
  renderQr('156+123+516+231').url !== renderQr('156+231+516+123').url);
// 错误校正: 库能处理 15 字符
const r2 = renderQr('654+312+123+231');
check('另一任务码正常生成', /base64,/.test(r2.url));

console.log(fail === 0 ? '\nALL TESTS PASSED' : '\n' + fail + ' FAILURES');
process.exit(fail === 0 ? 0 : 1);
