# ============================================================
# deploy_to_pi.ps1 —— 一键部署上位机套件到树莓派 4B
#
# 用法(在 Windows PowerShell 运行, 树莓派须同一局域网且 ssh 可达):
#   powershell -ExecutionPolicy Bypass -File deploy_to_pi.ps1
#   powershell -ExecutionPolicy Bypass -File deploy_to_pi.ps1 -Stamped        # 目录带时间戳
#   powershell -ExecutionPolicy Bypass -File deploy_to_pi.ps1 -WithExtras     # 连圆环库/回归一起覆盖
#
# 目标目录: /home/justin/vision_robot
# 时间戳模式: -Stamped → /home/justin/vision_robot_20260916_1224，并更新 vision_robot_current 软链
#
# 三台机器同一套代码: 树莓派 4B(~/vision_robot) / Jetson Orin Nano(~/VisualBot) / RDK X5(/root/VisualBot)
#
# [!] 本文件必须存成 UTF-8 with BOM: PowerShell 5.1 把无 BOM 的 UTF-8 当 GBK 读,
#     中文会变乱码并吃掉字符串引号 → "Unexpected token ')'" 这种莫名语法错误。
#
# 树莓派与另两台的差异:
#   · 算力最弱(4×A72), 性能档 auto → mid; 想更省用 --perf low;
#   · 相机是 UVC 定焦(≈17fps 上限), 不需要 --gst;
#   · 默认**不覆盖** Pi 上已有的 ring_center_detect_v2.py / qr_stream_demo.py / test_*.py
#     (它们本来就在), 要统一版本时加 -WithExtras 显式覆盖。
# ============================================================
param(
    [string]$Pi = "10.48.81.145",
    [string]$User = "justin",
    [string]$RemoteDir = "/home/justin/vision_robot",
    [switch]$WithLogic,
    [switch]$WithExtras,                                # 覆盖 Pi 上已有的圆环库/QR/回归
    [switch]$NoProbe,
    [switch]$Stamped,                                   # ★工程文件夹按时间命名: vision_robot_20260916_1224
    [string]$Stamp = (Get-Date -Format "yyyyMMdd_HHmm"),
    [switch]$NoLink,                                    # 不更新 current 软链
    [double]$ProbeSeconds = 2.0,
    [switch]$Force,                                     # ★强行覆盖远端"现场改动"文件(受保护清单见下)
    [string[]]$Protect = @("school_main.py")            # 受保护文件: 远端内容 != 本地内容 => 先备份远端再跳过
)

$ErrorActionPreference = "Continue"

$DEP = Split-Path -Parent $MyInvocation.MyCommand.Path          # ...\deploy_pi
$ROOT = Split-Path -Parent $DEP                                  # ...\整机程序设计
$EXT = Join-Path (Split-Path -Parent $ROOT) "_pdf_extract"        # ...\_pdf_extract
$SCHOOL_DIR = Join-Path $ROOT "校赛"                              # 校赛上位机主程序

# ★时间戳目录：-Stamped 时 RemoteDir 变成 <原名>_<时间戳>，并更新 <原名>_current 软链
$StableDir = $RemoteDir
if ($Stamped) { $RemoteDir = "${RemoteDir}_${Stamp}" }

$tools = @(
    # 视觉调参台(浏览器, 零窗口) + 相机/性能档公共库
    "ring_stream_ui.py", "monitor_ui.py", "color_blob_stream.py", "color_blob_demo.py",
    "digit_templates_ext.py",
    # 瞄准/标定/协议
    "aim_and_place.py", "calibrate_aim.py", "cam_diag_aim.py", "cam_probe.py",
    "find_cam.py", "smoke_calib.py", "tpl_time.py", "prof_loop.py",
    "test_aim_protocol.py",
    # 部署/探测/性能 + 调优三件套
    "deploy_to_jetson.ps1", "deploy_to_rdk.ps1",
    "probe_platform.py", "probe_jetson.py",
    "setup_platform.sh", "setup_jetson.sh",
    "bench_perf.sh", "bench_cam.sh",
    "prof_ui.py", "exp_speed.py", "exp_config.py",
    # 运维小脚本
    "kill_rsui.sh", "rsui_test.sh", "cbs_test.sh", "rdiag.sh",
    # 文档
    "README.md", "上位机指令对照表.md", "SSH命令大全.md"
)
# 圆环识别主库 + 三套回归 + QR 推流页（Pi 上通常已有 → 默认不传, 用 -WithExtras 覆盖）
$extras = @("ring_center_detect_v2.py", "qr_stream_demo.py", "test_rotation.py",
            "test_dashed.py", "test_stability.py")
$variants = @("aim_and_place_glare_filter.py", "aim_and_place_pi_lite.py")
# 校赛上位机主程序（只传 .py/.json/.md；firmware 是 Keil 用的，不进上位机目录）
# 校赛上位机文件：**自动列目录**（避免以后新增文件忘了加清单 —— 2026-09-16 就漏过 car.py/school_main.py）
$schoolFiles = @(Get-ChildItem -Path (Join-Path $SCHOOL_DIR "*.py") -File | Select-Object -ExpandProperty Name)
$schoolFiles += @(Get-ChildItem -Path (Join-Path $SCHOOL_DIR "*.json") -File | Select-Object -ExpandProperty Name)

Write-Host "==> 目标: ${User}@${Pi}:${RemoteDir}" -ForegroundColor Cyan
Write-Host "==> 探测树莓派在线状态 ..." -ForegroundColor Cyan
if (-not (Test-Connection -ComputerName $Pi -Count 1 -Quiet -ErrorAction SilentlyContinue)) {
    Write-Host "!! 树莓派 $Pi 不在线(ping 不通)。请确认:" -ForegroundColor Yellow
    Write-Host "    1) 树莓派已上电并连到同一网络"
    Write-Host "    2) IP 是否正确(可试 ssh pi / ping raspberrypi.local, 再改 ~/.ssh/config 的 HostName)"
    Write-Host "    3) 防火墙允许 ping/ssh"
    exit 1
}

$env:HOME = $env:USERPROFILE
ssh -o ConnectTimeout=8 -o BatchMode=yes $User@$Pi "echo SSH_OK" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! SSH 失败。检查 ~/.ssh/config 里 pi 的 User / IdentityFile(~/.ssh/pi_deploy) 是否还在" -ForegroundColor Yellow
    Write-Host "   (历史事故: IdentityFile 行丢失 -> Permission denied(publickey,password))" -ForegroundColor Gray
    exit 1
}
Write-Host "==> SSH OK, 准备远程目录 ..." -ForegroundColor Cyan
ssh $User@$Pi "mkdir -p $RemoteDir/digit_custom $RemoteDir/ring_ui_out $RemoteDir/color_demo_out $RemoteDir/校赛" 2>&1 | Out-Null

function Send-One([string]$localPath, [string]$destDir) {
    if (-not (Test-Path -LiteralPath $localPath)) {
        Write-Host "   !! 缺少 $localPath (跳过)" -ForegroundColor Yellow
        return
    }
    $name = Split-Path -Leaf $localPath
    scp -q -o ConnectTimeout=15 $localPath "${User}@${Pi}:${destDir}/${name}"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   !! scp 失败: $name" -ForegroundColor Red
    } else {
        Write-Host "   - $name" -ForegroundColor Green
    }
}

Write-Host "==> 传核心工具 + 文档 ($($tools.Count) 个) ..." -ForegroundColor Cyan
foreach ($f in $tools) { Send-One (Join-Path $DEP $f) $RemoteDir }

if ($WithExtras) {
    Write-Host "==> 覆盖圆环识别库 + 回归脚本 ($($extras.Count) 个, 来自 _pdf_extract) ..." -ForegroundColor Yellow
    foreach ($f in $extras) { Send-One (Join-Path $EXT $f) $RemoteDir }
} else {
    Write-Host "==> 跳过圆环库/回归(保留 Pi 上原有版本; 要覆盖加 -WithExtras)" -ForegroundColor Gray
}

Write-Host "==> 传视觉变体 ($($variants.Count) 个) ..." -ForegroundColor Cyan
foreach ($f in $variants) { Send-One (Join-Path $ROOT $f) $RemoteDir }

# ------------------------------------------------------------------
# ★现场改动保护(2026-09-16 事故: 部署把 RDK 上刚调好的参数覆盖了, 靠 VSCode 本地历史才救回)
#   受保护文件(默认 school_main.py)只要"远端内容 != 本地内容", 就说明板子上被现场改过:
#     ① 远端备份成 <名>.field_<Stamp>.bak   ② 本次跳过不覆盖(除非 -Force)
#   要把现场改动收进本地:  powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -Apply -Rdk 10.48.81.145 -User justin -RemoteDir /home/justin/vision_robot_current
# ------------------------------------------------------------------
$fieldDiffs = @()
foreach ($f in $Protect) {
    $remotePath = "$RemoteDir/校赛/$f"
    $localPath = Join-Path $SCHOOL_DIR $f
    if (-not (Test-Path -LiteralPath $localPath)) { continue }
    $remoteMd5 = (ssh $User@$Pi "md5sum '$remotePath' 2>/dev/null") -split '\s+' | Where-Object { $_ } | Select-Object -First 1
    if (-not $remoteMd5) { continue }                       # 远端还没有这个文件 -> 正常上传
    $localMd5 = (Get-FileHash -LiteralPath $localPath -Algorithm MD5).Hash.ToLower()
    if ($remoteMd5 -eq $localMd5) { continue }
    if ($Force) {
        ssh $User@$Pi "cp -a '$remotePath' '$remotePath.field_$Stamp.bak'" 2>&1 | Out-Null
        Write-Host "   !! -Force: 现场版已备份为 $f.field_$Stamp.bak, 本次覆盖" -ForegroundColor Yellow
    } else {
        $fieldDiffs += $f
    }
}
if ($fieldDiffs.Count -gt 0) {
    Write-Host ""
    Write-Host "!! 板子上这些文件有【现场改动】(与本地不同), 本次不覆盖:" -ForegroundColor Red
    foreach ($f in $fieldDiffs) { Write-Host "     · $f" -ForegroundColor Red }
    Write-Host "   要收进本地(推荐):  powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -Apply" -ForegroundColor Yellow
    Write-Host "   确认要覆盖(远端会先备份 .field_$Stamp.bak):  重跑并加 -Force" -ForegroundColor Yellow
    Write-Host ""
    $schoolFiles = @($schoolFiles | Where-Object { $fieldDiffs -notcontains $_ })
}

Write-Host "==> 传校赛主程序 ($($schoolFiles.Count) 个 → $RemoteDir/校赛) ..." -ForegroundColor Cyan
foreach ($f in $schoolFiles) { Send-One (Join-Path $SCHOOL_DIR $f) "$RemoteDir/校赛" }

if ($WithLogic) {
    $logicDir = Join-Path $ROOT "car_code上位机"
    Write-Host "==> 传 v2 纯逻辑层 (整机流程/任务码/串口执行) ..." -ForegroundColor Cyan
    ssh $User@$Pi "mkdir -p $RemoteDir/logic_v2" 2>&1 | Out-Null
    Get-ChildItem -Path (Join-Path $logicDir "*") -Include *.py, *.md -File -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { Send-One $_.FullName "$RemoteDir/logic_v2" }
}

Write-Host "==> 远程语法校验 ..." -ForegroundColor Cyan
ssh $User@$Pi "cd $RemoteDir && python3 -m py_compile ring_stream_ui.py color_blob_stream.py color_blob_demo.py digit_templates_ext.py aim_and_place.py probe_platform.py && cd 校赛 && python3 -m py_compile school_flow.py aim.py vision.py lower_api.py ledger.py qr_scan.py school_config.py && echo 'PY_COMPILE_OK'"

if (-not $NoLink) {
    Write-Host "==> 更新 current 软链: ${StableDir}_current -> $RemoteDir" -ForegroundColor Cyan
    ssh $User@$Pi "ln -sfn $RemoteDir ${StableDir}_current && ls -ld ${StableDir}_current" 2>&1 | Out-String | Write-Host
}

if (-not $NoProbe) {
    Write-Host "==> 平台/相机探测(约 $([int]$ProbeSeconds * 4) 秒; 期间别让别的程序占用相机) ..." -ForegroundColor Cyan
    ssh $User@$Pi "cd $RemoteDir && timeout 120 python3 -u probe_platform.py --seconds $ProbeSeconds"
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  部署完成!  远程工程目录: $RemoteDir" -ForegroundColor Green
if (-not $NoLink) { Write-Host "  固定入口: ${StableDir}_current -> $RemoteDir" -ForegroundColor Gray }
Write-Host "  树莓派上的启动步骤(与 Jetson/RDK 一致):" -ForegroundColor Green
Write-Host "  0. 进目录:      cd $RemoteDir" -ForegroundColor White
Write-Host "  1. 圆环调参台:  python3 ring_stream_ui.py   --perf auto --cam 0 --port 8091" -ForegroundColor White
Write-Host "  2. 颜色调参台:  python3 color_blob_stream.py --perf auto --cam 0 --port 8090" -ForegroundColor White
Write-Host "  3. 瞄准放置:    python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8092" -ForegroundColor White
Write-Host "  4. 校赛主程序:  cd 校赛 && python3 test_school_local.py && python3 -u school_flow.py --dry-run" -ForegroundColor White
Write-Host "  Pi4 算力有限, 建议 --perf mid 或 low; 性能对比: bash bench_perf.sh 12" -ForegroundColor Gray
Write-Host "  确认下位机已烧录最新固件(含 0x10~0x16 命令)" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor Green
