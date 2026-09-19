# =====================================================
# 工创赛上位机程序 —— VSCode 配置文件(Profile)一键创建脚本
# 作用: 创建名为"工创赛上位机程序"的独立 VSCode 配置文件,
#       只给该配置安装本项目所需扩展, 与其他项目的插件/设置
#       完全隔离(扩展只在该配置内启用, 配置只在该配置内生效)。
# 依赖: VSCode(code 命令)、Node.js(用于注册助手脚本)。
# 用法: 右键"使用 PowerShell 运行", 或
#       powershell -ExecutionPolicy Bypass -File "本脚本"
# =====================================================
param(
    [switch]$SkipPause   # 自动化/非交互运行: 跳过"VSCode运行中"的暂停确认
)
$ErrorActionPreference = 'Stop'

$ProfileName   = '工创赛上位机程序'
$ProjectFolder = 'E:\工程文档\27工创赛'
$HelperJs      = Join-Path $PSScriptRoot 'vscode_profile_helper.js'

# 本项目所需扩展(仅安装到该配置文件)
$Extensions = @(
    'ms-python.python',
    'ms-python.flake8',
    'ms-python.autopep8',
    'ms-vscode-remote.remote-ssh',
    'ms-vscode-remote.vscode-remote-extensionpack',
    'ms-ceintl.vscode-language-pack-zh-hans',
    'yzhang.markdown-all-in-one'
)

Write-Host '=============================================='
Write-Host " 创建 VSCode 配置文件: $ProfileName"
Write-Host '=============================================='

# 0) 前置检查
$codeCmd = Get-Command code -ErrorAction SilentlyContinue
if (-not $codeCmd) {
    Write-Host '[错误] 未找到 code 命令。请确认 VSCode 已安装并勾选"添加到 PATH"。' -ForegroundColor Red
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host '[错误] 未找到 node 命令(注册助手需要)。' -ForegroundColor Red
    exit 1
}
Write-Host ("VSCode 版本: " + (code --version)[0])

$codeRunning = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue).Count
if ($codeRunning -gt 0) {
    Write-Host "[提示] 检测到 VSCode 正在运行($codeRunning 个进程)。" -ForegroundColor Yellow
    Write-Host '      建议先完全退出 VSCode 再运行本脚本, 否则新配置可能要到重启后才可见。'
    if ($SkipPause) {
        Write-Host '      (-SkipPause: 跳过确认, 继续执行)' -ForegroundColor Yellow
    } else {
        Write-Host '      按回车继续(不退出), 或 Ctrl+C 取消后重来...' -ForegroundColor Yellow
        Read-Host
    }
}

# 1) 备份 storage.json(配置文件注册表)
$sj = Join-Path $env:APPDATA 'Code\User\globalStorage\storage.json'
if (Test-Path $sj) {
    $bak = "$sj.bak_工创赛"
    Copy-Item $sj $bak -Force
    Write-Host "[OK] 已备份注册表: $bak"
}

# 2) 注册配置文件 + 创建目录 + 写通用设置(助手脚本, 幂等)
Write-Host '注册配置文件(如已存在则跳过)...'
$helperOut = & node $HelperJs $ProfileName
$helperOut | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Host '[错误] 注册失败。' -ForegroundColor Red
    exit 1
}

# 3) 给该配置文件安装扩展
Write-Host '安装扩展(仅安装到该配置文件)...'
foreach ($ext in $Extensions) {
    Write-Host "  - $ext"
    & code --profile $ProfileName --install-extension $ext 2>&1 | Out-Null
    Start-Sleep -Milliseconds 600
}

# 4) 验证
$profilesRoot = Join-Path $env:APPDATA 'Code\User\profiles'
$sjNow = Get-Content $sj -Raw -Encoding UTF8 | ConvertFrom-Json
$prof = $sjNow.userDataProfiles | Where-Object { $_.name -eq $ProfileName }
if ($prof) {
    $extFile = Join-Path $profilesRoot ($prof.location + '\extensions.json')
    if (Test-Path $extFile) {
        $extArr = Get-Content $extFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $cnt = @($extArr).Count
        Write-Host "[OK] 配置文件 '$ProfileName' 已注册(目录 $($prof.location)), 已启用扩展 $cnt 个。"
    }
} else {
    Write-Host '[警告] 未在注册表中找到该配置, 请完全重启 VSCode 后再运行一次。' -ForegroundColor Yellow
}

# 5) 完成提示
Write-Host ''
Write-Host '================= 完成 ================='
Write-Host "1) 请【完全退出并重启】VSCode(关闭所有窗口)以加载新配置。"
Write-Host "2) 使用: 点击窗口左下角头像旁的"配置文件"按钮 → 选择 '$ProfileName'"
Write-Host "   或命令行: code --profile '$ProfileName' '$ProjectFolder'"
Write-Host "3) 项目专属扩展设置(flake8/autopep8等)已放在 .vscode\settings.json, 打开项目自动生效。"
Write-Host '=========================================='
