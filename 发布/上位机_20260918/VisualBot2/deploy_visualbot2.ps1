# ============================================================
# deploy_visualbot2.ps1 —— 部署 VisualBot2（**只跑图**）到 RDK X5
#
# 目标：/root/VisualBot2   —— 与老工程 /root/VisualBot **并存、互不影响**
#   · VisualBot2  = 底盘路线 + base 作业姿态 + 双摄推流 + 扫码 + 走位标定助手
#   · VisualBot   = 完整工程（含机械臂/龙门架/取料放置动作组），原封不动
#
# 用法（Windows PowerShell）：
#   powershell -ExecutionPolicy Bypass -File deploy_visualbot2.ps1
#   powershell -ExecutionPolicy Bypass -File deploy_visualbot2.ps1 -Rdk 10.48.81.249
#
# [!] 本文件必须存成 UTF-8 with BOM（PowerShell 5.1 把无 BOM 的 UTF-8 当 GBK 读，中文会吃掉引号）。
# ============================================================
param(
    [string]$Rdk = "rdk",
    [string]$User = "root",
    [string]$RemoteDir = "/root/VisualBot2",
    [switch]$NoProbe,
    [switch]$Force,                                     # ★强行覆盖远端"现场改动"文件（覆盖前自动备份）
    [string[]]$Protect = @("route_main.py")             # 受保护文件：跑图参数全在这里，现场就是在板上调它
)

$ErrorActionPreference = "Continue"

$DEP    = Split-Path -Parent $MyInvocation.MyCommand.Path       # ...\整机程序设计\VisualBot2
$ROOT   = Split-Path -Parent $DEP                               # ...\整机程序设计
$SCHOOL = Join-Path $ROOT "校赛"                                 # 复用：协议/视觉/运维
$PI     = Join-Path $ROOT "deploy_pi"                            # 复用：推流/调参台/识别库
$EXT    = Join-Path (Split-Path -Parent $ROOT) "_pdf_extract"    # 上游圆环识别库

Write-Host "==> 目标: ${User}@${Rdk}:${RemoteDir}" -ForegroundColor Cyan
if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
ssh -o ConnectTimeout=8 -o BatchMode=yes $User@$Rdk "echo SSH_OK" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! RDK $Rdk ping 不通，先确认板子在线/同网段" -ForegroundColor Yellow
    exit 1
}
$env:HOME = $env:USERPROFILE
ssh -o ConnectTimeout=8 $User@$Rdk "mkdir -p $RemoteDir" 2>&1 | Out-Null

# 分组：来源目录 + 文件名清单
$groups = @(
    @{ name = "VisualBot2 跑图 + 取料调试（新写的）"; dir = $DEP;
       files = @("route_car.py", "route_main.py", "route_debug.py",
                 "grab_demo.py", "place_demo.py", "target_stream_ui.py", "README.md") },
    @{ name = "协议 / 视觉 / 机械臂动作 / 运维（从校赛复用）"; dir = $SCHOOL;
       files = @("lower_api.py", "vision.py", "cam_rotate.py", "check_env.py",
                 "exposure_tune.py", "qr_scan.py", "school_config.py",
                 "car.py", "aim.py", "ledger.py") },
    @{ name = "推流与调参台（从 deploy_pi 复用）";    dir = $PI;
       files = @("monitor_ui.py", "ring_stream_ui.py", "color_blob_stream.py", "color_blob_demo.py") },
    @{ name = "圆环识别库（上游）";                  dir = $EXT;
       files = @("ring_center_detect_v2.py") }
)

# ------------------------------------------------------------------
# ★现场改动保护（2026-09-17 事故：我第一次部署 VisualBot2 时是无条件 scp，
#   把车主在板上刚调好的路线参数 TO_PROCESS/SPIN_DEG/ENTER_PROCESS/TO_FINISH 冲掉了）
#   受保护文件只要"远端内容 != 本地内容"，就说明板子上被现场改过：
#     ① 远端备份成 <名>.field_<时间戳>.bak   ② 本次跳过不覆盖（除非 -Force）
#   要把现场改动收回来：
#     powershell -File ..\deploy_pi\pull_from_rdk.ps1 -RemoteDir /root/VisualBot2 -LocalSchool <本地目录>
# ------------------------------------------------------------------
$fieldDiffs = @()
foreach ($f in $Protect) {
    $remotePath = "$RemoteDir/$f"
    $localPath = Join-Path $DEP $f
    if (-not (Test-Path -LiteralPath $localPath)) { continue }
    $remoteMd5 = (ssh $User@$Rdk "md5sum '$remotePath' 2>/dev/null") -split '\s+' |
                 Where-Object { $_ } | Select-Object -First 1
    if (-not $remoteMd5) { continue }                   # 远端还没这个文件 → 正常上传
    $localMd5 = (Get-FileHash -LiteralPath $localPath -Algorithm MD5).Hash.ToLower()
    if ($remoteMd5 -eq $localMd5) { continue }
    if ($Force) {
        $stamp = Get-Date -Format "yyyyMMdd_HHmm"
        ssh $User@$Rdk "cp -a '$remotePath' '$remotePath.field_$stamp.bak'" 2>&1 | Out-Null
        Write-Host "   !! -Force：现场版已备份为 $f.field_$stamp.bak，本次覆盖" -ForegroundColor Yellow
    } else {
        $fieldDiffs += $f
    }
}
if ($fieldDiffs.Count -gt 0) {
    Write-Host ""
    Write-Host "!! 板子上这些文件有【现场改动】（与本地不同），本次**不覆盖**:" -ForegroundColor Red
    foreach ($f in $fieldDiffs) { Write-Host "     · $f" -ForegroundColor Red }
    Write-Host "   收进本地：powershell -File ..\deploy_pi\pull_from_rdk.ps1 -RemoteDir $RemoteDir -LocalSchool <本地目录>" -ForegroundColor Yellow
    Write-Host "   确认覆盖（远端会先备份）：重跑并加 -Force" -ForegroundColor Yellow
    Write-Host ""
}

$total = 0
foreach ($g in $groups) {
    Write-Host "==> $($g.name) ..." -ForegroundColor Cyan
    foreach ($f in $g.files) {
        if ($fieldDiffs -contains $f) {
            Write-Host "   - $f（跳过：板子上有现场改动，保护中）" -ForegroundColor Yellow
            continue
        }
        $p = Join-Path $g.dir $f
        if (-not (Test-Path -LiteralPath $p)) {
            Write-Host "   !! 缺少 $f（跳过）" -ForegroundColor Yellow
            continue
        }
        scp -q -o ConnectTimeout=15 $p "${User}@${Rdk}:${RemoteDir}/$f"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   - $f" -ForegroundColor Green
            $total++
        } else {
            Write-Host "   !! scp 失败: $f" -ForegroundColor Red
        }
    }
}

Write-Host "==> 远程语法校验（编译目录下全部 .py）..." -ForegroundColor Cyan
ssh $User@$Rdk "cd $RemoteDir && python3 -m py_compile *.py && echo PY_COMPILE_OK"

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  VisualBot2 部署完成：$RemoteDir（$total 个文件）" -ForegroundColor Green
Write-Host "  ① 跑图（分段、每段停下等回车）：" -ForegroundColor White
Write-Host "       cd $RemoteDir && python3 -u route_main.py --port /dev/stm32" -ForegroundColor Gray
Write-Host "  ② 走位标定（发一段→你量→给建议值）：" -ForegroundColor White
Write-Host "       python3 -u route_debug.py --port /dev/stm32" -ForegroundColor Gray
Write-Host "  ③ 看画面：http://${Rdk}:8092/  （跑图时自动开双摄推流）" -ForegroundColor White
Write-Host "  ④ 查环境：python3 -u check_env.py" -ForegroundColor White
Write-Host "  ※ 老工程 /root/VisualBot 原封不动（机械臂那套还在），两边可随时对照" -ForegroundColor Gray
Write-Host "==============================================================" -ForegroundColor Green
