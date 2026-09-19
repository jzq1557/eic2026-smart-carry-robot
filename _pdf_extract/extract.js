const fs = require('fs');
const path = require('path');
const pdfParse = require('pdf-parse');

async function extract(file, out) {
  const buf = fs.readFileSync(file);
  const data = await pdfParse(buf);
  fs.writeFileSync(out, data.text, 'utf8');
  console.log(`OK ${path.basename(file)}: ${data.numpages} pages, ${data.text.length} chars -> ${out}`);
}

(async () => {
  const base = 'E:\\工程文档\\27工创赛';
  await extract(path.join(base, '附件2-1-2026第十五届上海工创大赛暨2027第十届国赛-智能+工程创新赛道命题与运行（发布稿）.pdf'), path.join(base, '_pdf_extract', '附件2-1命题与运行.txt'));
  await extract(path.join(base, '附件2-2-2026第十五届上海工创大赛暨2027第十届国赛-智能+工程创新赛道评分与规则（发布稿）.pdf'), path.join(base, '_pdf_extract', '附件2-2评分与规则.txt'));
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
