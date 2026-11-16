# ============================================================
# pull_from_rdk.ps1 —— 反向部署：把 RDK X5 上的【现场改动】取回本地工程
#
# 为什么需要它(2026-09-16 事故):
#   调参是在板子上做的(Windows 侧 VSCode Remote-SSH 直接改 RDK 上的 school_main.py),
#   而 deploy_to_rdk.ps1 是无条件 scp 覆盖 —— 一次部署就把刚调好的现场参数冲掉了。
#   现场版只剩 VSCode 服务端的本地历史(/root/.vscode-server/data/User/History)能救。
#   所以规矩改成: 部署前先 pull(Pull 会让本地跟上现场), 部署时受保护文件默认不覆盖。
#
# 用法(Windows PowerShell):
#   powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1                 # 只比对, 列差异, 不改任何文件
#   powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -Apply          # 把远端有差异的文件拉回本地(先备份本地)
#   powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -Apply -Only school_main.py,car.py
#   powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -WithHistory    # 顺便把 VSCode 历史快照打包存档
#   powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -Rdk 10.48.81.108 -User jetson -RemoteDir /home/jetson/VisualBot_current   # Jetson / Pi 同样适用
#   （RDK 默认就是 /root/VisualBot，无需 -RemoteDir；换机器见上一行的 Jetson/Pi 写法）
#
# [!] 本文件必须存成 UTF-8 with BOM(PowerShell 5.1 把无 BOM 的 UTF-8 当 GBK 读, 中文会吃掉引号)。
# ============================================================
param(
    [string]$Rdk = "rdk",
    [string]$User = "root",
    [string]$RemoteDir = "/root/VisualBot",           # ★2026-09-17 起 RDK 上就固定这个路径（不再有时间戳目录/软链）
    [string]$LocalSchool = "",                        # 默认 = 本脚本上级目录\校赛
    [switch]$Apply,                                   # 不加就只看差异
    [string[]]$Only = @(),                            # 只处理这些文件名(默认全部)
    [switch]$WithHistory,                             # 打包远端 VSCode 本地历史
    [string]$Stamp = (Get-Date -Format "yyyyMMdd_HHmm")
)

$ErrorActionPreference = "Continue"

$DEP = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT = Split-Path -Parent $DEP
if (-not $LocalSchool) { $LocalSchool = Join-Path $ROOT "校赛" }
if (-not (Test-Path -LiteralPath $LocalSchool)) {
    Write-Host "!! 本地目录不存在: $LocalSchool (用 -LocalSchool 指定)" -ForegroundColor Red
    exit 1
}
$BackupRoot = Join-Path $ROOT "_field_backup\$Stamp"

# ★远端结构探测：老工程是 <RemoteDir>/校赛/，VisualBot2 是扁平结构（文件直接在 <RemoteDir>/ 下）
$sub = if ((ssh -o ConnectTimeout=10 $User@$Rdk "test -d '$RemoteDir/校赛' && echo YES || echo NO" 2>$null).Trim() -eq "YES") { "校赛/" } else { "" }
Write-Host "==> 远端: ${User}@${Rdk}:${RemoteDir}/${sub}" -ForegroundColor Cyan
Write-Host "==> 本地: $LocalSchool" -ForegroundColor Cyan

if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
ssh -o ConnectTimeout=8 -o BatchMode=yes $User@$Rdk "echo SSH_OK" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! RDK X5 $Rdk ping 不通, 先确认板子在线/同网段" -ForegroundColor Yellow
    exit 1
}
$env:HOME = $env:USERPROFILE

# ---- 1. 远端所有 py 的 md5 ----
$remoteLines = ssh -o ConnectTimeout=10 $User@$Rdk "cd '$RemoteDir/$sub' && md5sum *.py 2>/dev/null"
if ($LASTEXITCODE -ne 0 -or -not $remoteLines) {
    Write-Host "!! 取远端 md5 失败(目录不存在?): $RemoteDir/$sub" -ForegroundColor Red
    exit 1
}
$remote = @{}
foreach ($line in $remoteLines) {
    $parts = ($line -split '\s+') | Where-Object { $_ }
    if ($parts.Count -ge 2) { $remote[$parts[-1]] = $parts[0].ToLower() }
}

# ---- 2. 本地 md5 ----
$local = @{}
Get-ChildItem -LiteralPath $LocalSchool -Filter *.py -File | ForEach-Object {
    $local[$_.Name] = (Get-FileHash -LiteralPath $_.FullName -Algorithm MD5).Hash.ToLower()
}

# ---- 3. 比对 ----
$names = @($remote.Keys + $local.Keys | Sort-Object -Unique)
if ($Only.Count -gt 0) { $names = $names | Where-Object { $Only -contains $_ } }

$diffRemoteNewer = @()   # 远端有、且与本地不同  = 板子上被改过(或本地还没有)
$onlyLocal       = @()   # 只本地有 = 还没部署上去
$same            = @()
foreach ($n in $names) {
    if ($remote.ContainsKey($n) -and $local.ContainsKey($n)) {
        if ($remote[$n] -eq $local[$n]) { $same += $n } else { $diffRemoteNewer += $n }
    } elseif ($remote.ContainsKey($n)) { $diffRemoteNewer += $n }
    else { $onlyLocal += $n }
}

Write-Host ""
Write-Host "== 比对结果 ==" -ForegroundColor Cyan
Write-Host ("   相同 {0} 个 | 与本地不同 {1} 个 | 只在本地 {2} 个" -f $same.Count, $diffRemoteNewer.Count, $onlyLocal.Count)
if ($diffRemoteNewer.Count -gt 0) {
    Write-Host "   [板子上与本地不同的文件](这些就是可能被现场改过的)" -ForegroundColor Yellow
    foreach ($n in $diffRemoteNewer) { Write-Host "     · $n" -ForegroundColor Yellow }
}
if ($onlyLocal.Count -gt 0) {
    Write-Host "   [只在本地有, 板上还没有(部署时会补上)]" -ForegroundColor Gray
    foreach ($n in $onlyLocal) { Write-Host "     · $n" -ForegroundColor Gray }
}

# ---- 4. 取回 ----
if ($diffRemoteNewer.Count -eq 0) {
    Write-Host ""
    Write-Host "==> 板子上没有比本地新的改动, 无需取回。" -ForegroundColor Green
} elseif (-not $Apply) {
    Write-Host ""
    Write-Host "==> 只是查看(未改动任何文件)。要取回请重跑并加 -Apply:" -ForegroundColor Cyan
    Write-Host "     powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -Apply" -ForegroundColor White
} else {
    New-Item -ItemType Directory -Force -Path (Join-Path $BackupRoot "校赛") | Out-Null
    Write-Host ""
    Write-Host "==> 取回(本地原文件先备份到 $BackupRoot\校赛) ..." -ForegroundColor Cyan
    foreach ($n in $diffRemoteNewer) {
        $dest = Join-Path $LocalSchool $n
        if (Test-Path -LiteralPath $dest) {
            Copy-Item -LiteralPath $dest -Destination (Join-Path (Join-Path $BackupRoot "校赛") $n) -Force
        }
        scp -q -o ConnectTimeout=15 "${User}@${Rdk}:${RemoteDir}/${sub}$n" $dest
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   <- $n" -ForegroundColor Green
        } else {
            Write-Host "   !! 拉取失败: $n" -ForegroundColor Red
        }
    }
    Write-Host "==> 完成。备份目录: $BackupRoot" -ForegroundColor Green
    Write-Host "    建议先看一眼 diff 再部署:  git diff --stat  (或直接对比 $LocalSchool)" -ForegroundColor Gray
}

# ---- 5. 现场标定档案（★调参台把它们存在**运行时的 cwd**，通常是部署根目录）----
#   color_profile.json(颜色阈值) / ring_calib.json(圆环阈值+画面方向) / aim_offset.txt(瞄准偏移)
#   这些是现场调出来的成果，务必顺手收回存档 —— 别只留板子上那一份（被覆盖过就没了）。
Write-Host ""
Write-Host "==> 收现场标定档案 → $BackupRoot ..." -ForegroundColor Cyan
foreach ($c in @("color_profile.json", "ring_calib.json", "aim_offset.txt")) {
    $hit = $false
    foreach ($sub in @("", "校赛/")) {                       # 两处都试：调参台跑在哪个目录就存哪
        $tmp = Join-Path $env:TEMP ("rdk_calib_" + $c)
        scp -q -o ConnectTimeout=10 "${User}@${Rdk}:${RemoteDir}/${sub}$c" $tmp 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp)) {
            New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
            $dst = if ($sub) { Join-Path $BackupRoot ("校赛_" + $c) } else { Join-Path $BackupRoot $c }
            Copy-Item $tmp $dst -Force
            Remove-Item $tmp -Force
            Write-Host "   <- ${sub}$c" -ForegroundColor Green
            $hit = $true
        }
    }
    if (-not $hit) { Write-Host "   (远端没有 $c，跳过)" -ForegroundColor Gray }
}

# ---- 6. VSCode 本地历史快照(最后一道保险) ----
if ($WithHistory) {
    Write-Host ""
    Write-Host "==> 打包远端 VSCode 本地历史(现场版的最后保险) ..." -ForegroundColor Cyan
    $histDir = "/root/.vscode-server/data/User/History"
    ssh $User@$Rdk "test -d $histDir && tar czf /tmp/rdk_vscode_history.tgz -C $histDir . && echo HISTORY_OK"
    if ($LASTEXITCODE -eq 0) {
        New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
        $tgz = Join-Path $BackupRoot "rdk_vscode_history_$Stamp.tgz"
        scp -q "${User}@${Rdk}:/tmp/rdk_vscode_history.tgz" $tgz
        ssh $User@$Rdk "rm -f /tmp/rdk_vscode_history.tgz" | Out-Null
        if (Test-Path $tgz) { Write-Host "   已存: $tgz" -ForegroundColor Green }
    } else {
        Write-Host "   (远端没有 $histDir, 跳过)" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "提示: 部署前养成习惯 —— 先 pull_from_rdk.ps1 看差异, 再 deploy_to_rdk.ps1。" -ForegroundColor Gray
