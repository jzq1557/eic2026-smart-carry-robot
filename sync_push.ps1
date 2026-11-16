# sync_push.ps1 —— 把主目录的「上下位机」改动同步到发布仓库
#
# 背景：GitHub 上只放上下位机相关（约 9.5 MB）。主目录 E:\工程文档\27工创赛 里
#       还有 4.5 GB 的 上位机YOLO开发/、发票、CAD 等不该上传的东西。
#       所以主目录的 git 是旧历史（1.67 GiB），发布仓库是另一个干净仓库。
#
# 用法（在 PowerShell 里跑）：
#     powershell -ExecutionPolicy Bypass -File sync_push.ps1              # 同步 + 显示差异（不提交）
#     powershell -ExecutionPolicy Bypass -File sync_push.ps1 -Commit "fix: xxx"   # 同步 + 提交 + 推送
#
# 只同步这两处（与发布仓库一致）：
#     $SRC\整机程序设计   →  $DST\整机程序设计
#     $SRC\发布           →  $DST\发布
#     $SRC\*.md           →  $DST\
#
# 排除（编译产物 / 私有财务 / 现场备份）：
#     OBJ/、__pycache__/、_field_backup/、_field_snapshots/、
#     *.o *.crf *.d *.pyc *.hex、工创赛开票/
#
# ⚠️ 不使用 robocopy /MIR（那会删除目标里多出来的文件，太危险）；
#    只用 /E 增量复制 + 显式删除源里已不存在的文件（-Prune）。

param(
    [string]$Commit = "",                       # 给了就提交推送；不给只同步+显示差异
    [string]$Message = "",                      # 提交信息（等价写法）
    [string]$SRC = "E:\工程文档\27工创赛",
    [string]$DST = "E:\工程文档\_push_clean",
    [string]$Git = "C:\Program Files\Git\cmd\git.exe",
    [switch]$Prune                              # 额外：删除目标里源已不存在的文件
)

$ErrorActionPreference = "Stop"
$msg = if ($Commit) { $Commit } elseif ($Message) { $Message } else { "" }

function Write-Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }

if (-not (Test-Path $SRC)) { throw "源目录不存在: $SRC" }
if (-not (Test-Path "$DST\.git")) { throw "目标不是 git 仓库: $DST（缺 .git）" }

$XD = @("OBJ", "__pycache__", "_field_backup", "_field_snapshots", "工创赛开票", "Listings", "Objects", "DebugConfig", "RTE")
$XF = @("*.o", "*.crf", "*.d", "*.lst", "*.map", "*.htm", "*.pyc", "*.hex", "*.axf", "*.lnp", "*.dep")

function Sync-Dir($from, $to, $label) {
    if (-not (Test-Path $from)) { Write-Host "  (跳过 $label：源不存在)" -ForegroundColor DarkGray; return }
    Write-Step "同步 $label"
    $rcArgs = @($from, $to, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    foreach ($d in $XD) { $rcArgs += "/XD"; $rcArgs += $d }
    foreach ($f in $XF) { $rcArgs += "/XF"; $rcArgs += $f }
    & robocopy @rcArgs | Out-Null
    # robocopy: 0=无变化 1=有复制 2=有多余文件 3=1+2 ... 8+ 才是错误
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（退出码 $LASTEXITCODE）：$label" }
    Write-Host ("  完成（robocopy 退出码 {0}）" -f $LASTEXITCODE) -ForegroundColor Green
}

function Remove-Orphans($from, $to, $label) {
    if (-not $Prune) { return }
    if (-not (Test-Path $from)) { return }
    Write-Step "清理 $label 中源已不存在的文件（-Prune）"
    $srcSet = @{}
    Get-ChildItem $from -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $srcSet[$_.FullName.Substring($from.Length).TrimStart('\')] = $true
    }
    $removed = 0
    Get-ChildItem $to -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($to.Length).TrimStart('\')
        $skip = $false
        foreach ($d in $XD) { if ($rel -like "$d\*" -or $rel -like "*\$d\*") { $skip = $true } }
        if (-not $skip -and -not $srcSet.ContainsKey($rel)) {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue; $removed++
        }
    }
    Write-Host "  删除 $removed 个文件"
}

# ---- 1) 同步 ----
Sync-Dir "$SRC\整机程序设计" "$DST\整机程序设计" "整机程序设计/"
Sync-Dir "$SRC\发布"         "$DST\发布"         "发布/"
Remove-Orphans "$SRC\整机程序设计" "$DST\整机程序设计" "整机程序设计/"

Write-Step "同步根目录 *.md"
Get-ChildItem $SRC -Filter *.md -File -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $DST $_.Name) -Force
}
Copy-Item "$SRC\.gitignore" "$DST\.gitignore" -Force -ErrorAction SilentlyContinue     # 排除规则保持一致
Write-Host "  完成"

# ---- 2) 显示差异 ----
Write-Step "目标仓库差异"
Push-Location $DST
try {
    & $Git add -A
    $stat = & $Git status --short
    if (-not $stat) {
        Write-Host "  （无改动）" -ForegroundColor Green
        return
    }
    $stat | Select-Object -First 30
    if (($stat | Measure-Object).Count -gt 30) { Write-Host "  ...（还有更多）" }

    # ---- 3) 提交（只有显式给了信息才提交，避免误提交）----
    if ($msg) {
        Write-Step "提交"
        & $Git commit -q -m $msg
        & $Git log --oneline -1
        Write-Host ""
        Write-Host "  下一步：在 DSH 里用 git_push 插件推它 ——" -ForegroundColor Yellow
        Write-Host "          repo = $DST   remote = origin   branch = main" -ForegroundColor Yellow
    } else {
        Write-Host ""
        Write-Host "  已暂存但未提交。确认无误后重跑并带 -Commit ""提交信息""" -ForegroundColor Yellow
    }
}
finally { Pop-Location }
