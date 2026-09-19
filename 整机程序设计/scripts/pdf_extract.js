// PDF text extractor: FlateDecode + content stream + ToUnicode CMap, no external deps
const fs = require('fs');
const zlib = require('zlib');

const file = process.argv[2];
const outFile = process.argv[3] || (file.replace(/\.pdf$/i, '') + '.txt');
global.__DBG = process.env.PDFDBG === '1';

const data = fs.readFileSync(file);
const s = data.toString('latin1'); // charCodeAt == byte

// ---------- collect all objects ----------
const objects = new Map();
{
  const re = /(\d+)\s+(\d+)\s+obj/g;
  let m;
  while ((m = re.exec(s))) {
    const bodyStart = re.lastIndex;
    const end = s.indexOf('endobj', bodyStart);
    if (end < 0) break;
    objects.set(parseInt(m[1]), s.slice(bodyStart, end));
    re.lastIndex = end + 6;
  }
}

// ---------- extract stream from an object body ----------
function getStream(body) {
  const si = body.indexOf('stream');
  if (si < 0) return null;
  const dict = body.slice(0, si);
  let p = si + 6;
  if (body[p] === '\r') p++;
  if (body[p] === '\n') p++;
  let ei = body.lastIndexOf('endstream');
  let str = body.slice(p, ei);
  if (str.endsWith('\n')) str = str.slice(0, -1);
  if (str.endsWith('\r')) str = str.slice(0, -1);
  let buf = Buffer.from(str, 'latin1');
  const lm = /\/Length\s+(\d+)/.exec(dict);
  if (lm) buf = buf.subarray(0, parseInt(lm[1]));
  let inflated = null;
  try { inflated = zlib.inflateSync(buf); }
  catch (e) { try { inflated = zlib.inflateSync(Buffer.concat([Buffer.from([0x78, 0x9c]), buf])); } catch (e2) {} }
  return { dict, raw: buf, inflated };
}

// ---------- parse ToUnicode CMap ----------
function parseCMap(cmapStr) {
  const map = new Map();
  const hexToUnits = (hex) => {
    let u = '';
    for (let i = 0; i < hex.length; i += 4) u += String.fromCharCode(parseInt(hex.substr(i, 4), 16));
    return u;
  };
  let m;
  const bfcharRe = /beginbfchar([\s\S]*?)endbfchar/g;
  while ((m = bfcharRe.exec(cmapStr))) {
    const pairs = m[1].match(/<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>/g) || [];
    for (const p of pairs) {
      const pm = /<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>/.exec(p);
      map.set(parseInt(pm[1], 16), hexToUnits(pm[2]));
    }
  }
  const bfrangeRe = /beginbfrange([\s\S]*?)endbfrange/g;
  while ((m = bfrangeRe.exec(cmapStr))) {
    const rows = m[1].match(/<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f ]+>|\[[^\]]*\])/g) || [];
    for (const r of rows) {
      const rm = /<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<([0-9A-Fa-f ]+)>|\[([^\]]*)\])/.exec(r);
      if (!rm) continue;
      const lo = parseInt(rm[1], 16), hi = parseInt(rm[2], 16);
      if (rm[3].startsWith('<')) {
        const h = rm[4].trim();
        const base = parseInt(h, 16);
        for (let c = lo; c <= hi; c++) {
          const val = (base + (c - lo)).toString(16).padStart(h.length, '0');
          map.set(c, hexToUnits(val));
        }
      } else {
        const arr = (rm[5] || '').trim().split(/\s+/);
        let idx = 0;
        for (let c = lo; c <= hi && idx < arr.length; c++, idx++) {
          const h = arr[idx].replace(/[<>]/g, '');
          map.set(c, hexToUnits(h));
        }
      }
    }
  }
  return map;
}

// ---------- font resolution ----------
const cmapCache = new Map(); // fontObjId -> Map(cid -> str)
function getCMap(fontId) {
  if (cmapCache.has(fontId)) return cmapCache.get(fontId);
  const fontBody = objects.get(fontId) || '';
  let tuId = null;
  let m = /\/ToUnicode\s+(\d+)\s+\d+\s+R/.exec(fontBody);
  if (m) tuId = parseInt(m[1]);
  else {
    const dm = /\/DescendantFonts\s*\[\s*(\d+)\s+\d+\s+R/.exec(fontBody);
    if (dm) {
      const df = objects.get(parseInt(dm[1])) || '';
      m = /\/ToUnicode\s+(\d+)\s+\d+\s+R/.exec(df);
      if (m) tuId = parseInt(m[1]);
    }
  }
  let map = null;
  if (tuId != null) {
    const st = getStream(objects.get(tuId) || '');
    if (st && st.inflated) map = parseCMap(st.inflated.toString('latin1'));
  }
  cmapCache.set(fontId, map);
  return map;
}

function decodeString(bytes, fontId, fontBody) {
  if (/\/Subtype\s*\/Type0/.test(fontBody || '')) {
    const cmap = getCMap(fontId);
    let out = '';
    if (cmap) {
      for (let i = 0; i + 1 < bytes.length; i += 2) {
        const cid = (bytes[i] << 8) | bytes[i + 1];
        out += cmap.get(cid) || '';
      }
      return out;
    }
    return '';
  }
  // simple font: latin1 fallback
  return bytes.toString('latin1');
}

// ---------- tokenizer ----------
function tokenize(content) {
  const tokens = [];
  let i = 0;
  const n = content.length;
  while (i < n) {
    const c = content[i];
    if (/\s/.test(c)) { i++; continue; }
    if (c === '%') { while (i < n && content[i] !== '\n') i++; continue; }
    if (c === '(') {
      let depth = 1, j = i + 1, out = '';
      while (j < n && depth > 0) {
        const ch = content[j];
        if (ch === '\\') {
          const e = content[j + 1];
          if (e === 'n') { out += '\n'; j += 2; }
          else if (e === 'r') { out += '\r'; j += 2; }
          else if (e === 't') { out += '\t'; j += 2; }
          else if (e === 'b') { out += '\b'; j += 2; }
          else if (e === 'f') { out += '\f'; j += 2; }
          else if (e === '(' || e === ')' || e === '\\') { out += e; j += 2; }
          else if (/\d/.test(e || '')) {
            let o = 0, k = 0;
            j++;
            while (k < 3 && j < n && /\d/.test(content[j])) { o = o * 10 + (content.charCodeAt(j) - 48); j++; k++; }
            out += String.fromCharCode(o & 0xff);
          } else { out += e || ''; j += 2; }
        } else if (ch === '(') { depth++; out += ch; j++; }
        else if (ch === ')') { depth--; if (depth > 0) out += ch; j++; }
        else { out += ch; j++; }
      }
      tokens.push({ t: 'str', v: out });
      i = j;
      continue;
    }
    if (c === '<') {
      if (content[i + 1] === '<') {
        const e = content.indexOf('>>', i + 2);
        tokens.push({ t: 'dict', v: content.slice(i + 2, e) });
        i = (e < 0 ? i + 2 : e + 2);
        continue;
      }
      const e = content.indexOf('>', i + 1);
      tokens.push({ t: 'hex', v: content.slice(i + 1, e < 0 ? i + 1 : e) });
      i = (e < 0 ? i + 1 : e + 1);
      continue;
    }
    if (c === '[') { tokens.push({ t: '[' }); i++; continue; }
    if (c === ']') { tokens.push({ t: ']' }); i++; continue; }
    let j = i;
    while (j < n && !/[\s\[\]<>(){}]/.test(content[j])) j++;
    tokens.push({ t: 'tok', v: content.slice(i, j) });
    i = j;
  }
  return tokens;
}

// ---------- parse one content stream with given resources ----------
function parseContent(inflatedLatin, resources, lines, pageBox) {
  if (!inflatedLatin) return;
  const toks = tokenize(inflatedLatin);
  let fontName = null, fontId = null, fontBody = null;
  let size = 12, tx = 0, ty = 0, tl = 0;
  let inText = false;
  const fonts = {};
  const fm = resources.match(/\/Font\s*<<([\s\S]*?)>>/);
  if (fm) {
    const fre = /\/([\w.]+)\s+(\d+)\s+\d+\s+R/g;
    let mm;
    while ((mm = fre.exec(fm[1]))) fonts[mm[1]] = parseInt(mm[2]);
  }
  const xobjects = {};
  const xm = resources.match(/\/XObject\s*<<([\s\S]*?)>>/);
  if (xm) {
    const xre = /\/([\w.]+)\s+(\d+)\s+\d+\s+R/g;
    let mm;
    while ((mm = xre.exec(xm[1]))) xobjects[mm[1]] = parseInt(mm[2]);
  }

  function setFont(name, sz) {
    fontName = name;
    size = sz;
    const id = fonts[name.replace(/^\//, '')];
    if (global.__DBG) console.log('SETFONT', name, '->', id, 'fontsKeys:', Object.keys(fonts).join(','));
    if (id != null) {
      fontId = id;
      fontBody = objects.get(id) || '';
    }
  }
  function show(strTok) {
    if (fontId == null) return;
    let bytes;
    if (strTok.t === 'hex') {
      const hex = strTok.v.replace(/\s/g, '');
      bytes = hex ? Buffer.from(hex, 'hex') : Buffer.alloc(0);
    } else bytes = Buffer.from(strTok.v, 'latin1');
    const text = decodeString(bytes, fontId, fontBody);
    if (global.__DBG) console.log('SHOW', fontId, JSON.stringify(bytes.toString('hex')), JSON.stringify(text), 'tx', tx, 'ty', ty);
    if (text && text.trim()) lines.push({ x: tx, y: ty, text });
  }

  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    if (t.t === 'tok') {
      const v = t.v;
      if (v === 'BT') { inText = true; continue; }
      if (v === 'ET') { inText = false; continue; }
      if (!inText) continue;
      if (v === 'Tf' && i >= 2 && toks[i - 2].t === 'tok' && toks[i - 1].t === 'tok') {
        setFont(toks[i - 2].v, parseFloat(toks[i - 1].v)); continue;
      }
      if (v === 'Tm' && i >= 6) {
        tx = parseFloat(toks[i - 2].v); ty = parseFloat(toks[i - 1].v); continue;
      }
      if (v === 'Td' && i >= 2) {
        tx += parseFloat(toks[i - 2].v); ty += parseFloat(toks[i - 1].v); continue;
      }
      if (v === 'TD' && i >= 2) {
        tl = -parseFloat(toks[i - 1].v);
        tx += parseFloat(toks[i - 2].v); ty += parseFloat(toks[i - 1].v); continue;
      }
      if (v === 'T*') { ty -= tl; continue; }
      if (v === 'Tj' && i >= 1 && (toks[i - 1].t === 'str' || toks[i - 1].t === 'hex')) {
        show(toks[i - 1]); continue;
      }
      if (v === "'" && i >= 1 && (toks[i - 1].t === 'str' || toks[i - 1].t === 'hex')) {
        ty -= tl; show(toks[i - 1]); continue;
      }
      if (v === 'TJ' && i >= 1 && toks[i - 1].t === ']') {
        // walk back to '['
        let j = i - 2, items = [];
        while (j >= 0 && toks[j].t !== '[') { items.unshift(toks[j]); j--; }
        for (const it of items) if (it.t === 'str' || it.t === 'hex') show(it);
        continue;
      }
      if (v === 'Do' && i >= 1 && toks[i - 1].t === 'tok') {
        const xid = xobjects[toks[i - 1].v.replace(/^\//, '')];
        if (xid != null) {
          const xb = objects.get(xid) || '';
          if (/\/Subtype\s*\/Form/.test(xb)) {
            const st = getStream(xb);
            if (st && st.inflated) {
              const xr = /\/Resources\s*<<([\s\S]*?)\/ProcSet/.exec(xb) || /\/Resources\s*(\d+\s+\d+\s+R)/.exec(xb);
              let res = resources;
              if (xr && xr[1].includes('Font')) res = '<<' + xr[1].split(/>>/)[0] + '>>';
              else if (xr) {
                const robj = objects.get(parseInt(xr[1]));
                if (robj) res = robj;
              }
              parseContent(st.inflated.toString('latin1'), res, lines, pageBox);
            }
          }
        }
        continue;
      }
    }
  }
}

// ---------- pages ----------
const pages = [];
for (const [id, body] of objects) {
  if (/\/Type\s*\/Page\b/.test(body)) {
    const rm = /\/Resources\s*<<([\s\S]*?)>>/.exec(body);
    let resources = rm ? '<<' + rm[1] + '>>' : '';
    if (!rm) {
      const rr = /\/Resources\s*(\d+)\s+\d+\s+R/.exec(body);
      if (rr) resources = objects.get(parseInt(rr[1])) || '';
    }
    const mbm = /\/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/.exec(body);
    const pageBox = mbm ? mbm.slice(1).map(parseFloat) : null;
    const cids = [];
    const cm = /\/Contents\s*(\d+)\s+\d+\s+R/.exec(body);
    if (cm) cids.push(parseInt(cm[1]));
    else {
      const ca = /\/Contents\s*\[([^\]]*)\]/.exec(body);
      if (ca) {
        const cre = /(\d+)\s+\d+\s+R/g;
        let mm;
        while ((mm = cre.exec(ca[1]))) cids.push(parseInt(mm[1]));
      }
    }
    pages.push({ id, resources, cids, pageBox });
  }
}
pages.sort((a, b) => a.id - b.id);

// ---------- run ----------
const out = [];
for (const pg of pages) {
  const lines = [];
  for (const cid of pg.cids) {
    const st = getStream(objects.get(cid) || '');
    if (global.__DBG) console.log('PAGE', pg.id, 'CID', cid, 'inflated?', !!(st && st.inflated), 'resources:', pg.resources.slice(0, 80));
    if (st && st.inflated) parseContent(st.inflated.toString('latin1'), pg.resources, lines, pg.pageBox);
    if (global.__DBG) console.log('PAGE', pg.id, 'LINES:', lines.length);
  }
  out.push(`\n========== Page (obj ${pg.id}) ==========`);
  // group by y (desc = top first), sort by x within line
  const grouped = new Map();
  for (const l of lines) {
    const key = Math.round(l.y * 4) / 4;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(l);
  }
  const ys = [...grouped.keys()].sort((a, b) => b - a);
  for (const y of ys) {
    const items = grouped.get(y).sort((a, b) => a.x - b.x);
    out.push(items.map(i => i.text).join(''));
  }
}
fs.writeFileSync(outFile, out.join('\n'), 'utf8');
console.log('wrote', outFile, out.length, 'lines');
