# =====================================================
# 工创赛上位机程序 —— VSCode 配置文件(Profile)一键创建脚本
# 作用: 创建名为"工创赛上位机程序"的独立 VSCode 配置文件,
#       并只给该配置安装本项目所需扩展, 与其他项目的
#       插件/设置完全隔离。
# 用法: 右键"使用 PowerShell 运行", 或在终端执行
#       powershell -ExecutionPolicy Bypass -File "本脚本路径"
# =====================================================
$ErrorActionPreference = 'Stop'

$ProfileName   = '工创赛上位机程序'
$ProjectFolder = 'E:\工程文档\27工创赛'

# 本项目所需的扩展(仅安装到该配置文件)
$Extensions = @(
    'ms-python.python',
    'ms-python.flake8',
    'ms-python.autopep8',
    'ms-vscode-remote.remote-ssh',
    'ms-vscode-remote.vscode-remote-extensionpack',
    'ms-ceintl.vscode-language-pack-zh-hans',
    'yzhang.markdown-all-in-one'
)

# 写入该配置文件的通用设置(项目专属的 flake8/autopep8 等仍由 .vscode\settings.json 承担)
$ProfileSettings = @{
    'editor.tabSize'                 = 4
    'editor.insertSpaces'            = $true
    'files.encoding'                 = 'utf8'
    'files.autoGuessEncoding'        = $true
    'files.eol'                      = "`n"
    'terminal.integrated.defaultProfile.windows' = 'PowerShell'
    'terminal.integrated.env.windows' = @{ 'LC_ALL' = 'C' }
    'python.analysis.typeCheckingMode' = 'basic'
    'python.analysis.autoImportCompletions' = $true
    'python.terminal.activateEnvironment' = $false
}

Write-Host '=============================================='
Write-Host " 创建 VSCode 配置文件: $ProfileName"
Write-Host '=============================================='

# 0) 检查 code 命令
$codeCmd = Get-Command code -ErrorAction SilentlyContinue
if (-not $codeCmd) {
    Write-Host '[错误] 未找到 code 命令。请确认已安装 VSCode 并勾选"加入 PATH"。' -ForegroundColor Red
    exit 1
}
Write-Host ("VSCode 版本: " + (code --version)[0])

# 1) 记录当前已有 profile 目录(用于事后定位新建目录)
$profilesRoot = Join-Path $env:APPDATA 'Code\User\profiles'
$before = @(Get-ChildItem $profilesRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })

# 2) 用官方 CLI 创建配置文件(不存在则创建)并打开项目文件夹
Write-Host "创建/打开配置文件 '$ProfileName' ..."
& code --profile $ProfileName $ProjectFolder
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
    # code 启动类命令通常不设退出码, 忽略
}
Start-Sleep -Seconds 5

# 3) 给该配置文件安装扩展(CLI 会解析名称并写入对应 profile 的 extensions.json)
foreach ($ext in $Extensions) {
    Write-Host "  安装 $ext ..."
    & code --profile $ProfileName --install-extension $ext
    Start-Sleep -Milliseconds 800
}

# 4) 定位该配置文件的目录并写入通用设置
$after = @(Get-ChildItem $profilesRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
$newDirs = @($after | Where-Object { $_ -notin $before })
if ($newDirs.Count -eq 1) {
    $profileDir = Join-Path $profilesRoot $newDirs[0]
    Write-Host "配置文件目录: $profileDir"

    # 合并写入 settings.json(保留已有键)
    $settingsPath = Join-Path $profileDir 'settings.json'
    $existing = @{}
    if (Test-Path $settingsPath) {
        try { $existing = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable } catch { $existing = @{} }
    }
    foreach ($k in $ProfileSettings.Keys) { $existing[$k] = $ProfileSettings[$k] }
    $existing | ConvertTo-Json -Depth 6 | Set-Content -Path $settingsPath -Encoding UTF8
    Write-Host "[OK] 已写入配置文件级设置: settings.json"
} else {
    Write-Host '[提示] 未能定位新配置目录(配置文件可能已存在): 通用设置未重复写入;' -ForegroundColor Yellow
    Write-Host '      项目专属设置已由 .vscode\settings.json 自动生效。'
}

# 5) 完成提示
Write-Host ''
Write-Host '================= 完成 ================='
Write-Host "配置文件 '$ProfileName' 已就绪。"
Write-Host '1) 重新加载窗口生效: Ctrl+Shift+P → 输入"重新加载窗口"回车'
Write-Host "2) 以后使用: 点击窗口左下角头像旁的"配置文件"按钮 → 选择 '$ProfileName'"
Write-Host "   或命令行: code --profile '$ProfileName' 'E:\工程文档\27创赛'"
Write-Host '=========================================='
