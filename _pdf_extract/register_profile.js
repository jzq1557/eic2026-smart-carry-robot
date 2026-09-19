// 在 storage.json 的 userDataProfiles 中注册新 profile, 并创建其目录
const fs = require('fs');
const path = require('path');

const sjPath = path.join(process.env.APPDATA, 'Code', 'User', 'globalStorage', 'storage.json');
const name = '工创赛上位机程序';
const id = '-' + Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0');

const sj = JSON.parse(fs.readFileSync(sjPath, 'utf8'));
const list = sj.userDataProfiles || [];
if (list.some(p => p.name === name)) {
  console.log('ALREADY_EXISTS:', JSON.stringify(list.find(p => p.name === name)));
  process.exit(2);
}
list.push({ location: id, name: name, useDefaultFlags: {} });
sj.userDataProfiles = list;
fs.writeFileSync(sjPath, JSON.stringify(sj, null, 4), 'utf8');
console.log('REGISTERED:', JSON.stringify({ id, name }));

// 创建 profile 目录
const pdir = path.join(process.env.APPDATA, 'Code', 'User', 'profiles', id);
fs.mkdirSync(pdir, { recursive: true });
fs.mkdirSync(path.join(pdir, 'snippets'), { recursive: true });
fs.writeFileSync(path.join(pdir, 'settings.json'),
  JSON.stringify({
    'editor.tabSize': 4,
    'editor.insertSpaces': true,
    'files.encoding': 'utf8',
    'files.autoGuessEncoding': true,
    'files.eol': '\n',
    'terminal.integrated.defaultProfile.windows': 'PowerShell',
    'terminal.integrated.env.windows': { 'LC_ALL': 'C' },
    'python.analysis.typeCheckingMode': 'basic',
    'python.analysis.autoImportCompletions': true,
    'python.terminal.activateEnvironment': false
  }, null, 4), 'utf8');
fs.writeFileSync(path.join(pdir, 'extensions.json'), '[]');
console.log('PROFILE_DIR_CREATED:', pdir);
