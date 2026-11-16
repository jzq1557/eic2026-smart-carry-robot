# ============================================================
# deploy_to_rdk.ps1 —— 一键把上位机(视觉/推流/瞄准)套件部署到 RDK X5
#
# 用法(在 Windows PowerShell 运行; RDK X5 须同一局域网且 ssh 可达):
#   powershell -ExecutionPolicy Bypass -File deploy_to_rdk.ps1
#   powershell -ExecutionPolicy Bypass -File deploy_to_rdk.ps1 -Rdk 10.48.81.249
#   powershell -ExecutionPolicy Bypass -File deploy_to_rdk.ps1 -WithLogic
#
# 目标目录: /root/VisualBot   (RDK X5 默认 root 登录; 若是 sunrise 账号请用 -User sunrise -RemoteDir /home/sunrise/VisualBot)
#
# 三台机器同一套代码: 树莓派 4B(~/vision_robot) / Jetson Orin Nano(~/VisualBot) / RDK X5(~/VisualBot)
#
# [!] 本文件必须存成 UTF-8 with BOM: PowerShell 5.1 把无 BOM 的 UTF-8 当 GBK 读,
#     中文会变乱码并吃掉字符串引号 → "Unexpected token ')'" 这种莫名语法错误。
#
# RDK X5 与另两台的差异(脚本跑完的探测会再确认一次):
#   · OpenCV 是 apt 版 4.5.4, **不带 GStreamer** → 别用 --gst, 相机一律走 V4L2;
#   · 8×Cortex-A55, 纯 CPU 视觉; 没有 CUDA, 但有 BPU(本套件目前不用, 纯 OpenCV);
#   · MIPI CSI 摄像头由 hobot VPS 驱动, 出图后同样表现为 /dev/video* → 当 UVC 用;
#   · 出厂**没有 /dev/video*** 说明摄像头没插/驱动没加载(见 probe_platform.py 的提示)。
# ============================================================
param(
    [string]$Rdk = "rdk",
    [string]$User = "root",
    [string]$RemoteDir = "/root/VisualBot",
    [switch]$WithLogic,
    [switch]$NoProbe,
    [switch]$Stamped,                                   # ★工程文件夹按时间命名: VisualBot_20260916_1222
    [string]$Stamp = (Get-Date -Format "yyyyMMdd_HHmm"),
    [switch]$Link,                                      # ★2026-09-17 起默认**不建** current 软链
                                                        #   （目录名已固定为 /root/VisualBot，一个名字最不容易开错）
    [double]$ProbeSeconds = 2.0,
    [switch]$Force,                                     # ★强行覆盖远端"现场改动"文件(受保护清单见下)
    [string[]]$Protect = @("school_main.py")            # 受保护文件: 远端内容 != 本地内容 => 先备份远端再跳过
)

$ErrorActionPreference = "Continue"

$DEP = Split-Path -Parent $MyInvocation.MyCommand.Path          # ...\deploy_pi
$ROOT = Split-Path -Parent $DEP                                  # ...\整机程序设计
$EXT = Join-Path (Split-Path -Parent $ROOT) "_pdf_extract"        # ...\_pdf_extract
$SCHOOL_DIR = Join-Path $ROOT "校赛"                                  # 校赛上位机主程序

# ★时间戳目录：-Stamped 时 RemoteDir 变成 <原名>_<时间戳>，并更新 <原名>current 软链
$StableDir = $RemoteDir
if ($Stamped) { $RemoteDir = "${RemoteDir}_${Stamp}" }

$tools = @(
    # 瞄准/标定/协议
    "aim_and_place.py", "calibrate_aim.py", "cam_diag_aim.py", "cam_probe.py",
    "find_cam.py", "smoke_calib.py", "tpl_time.py", "prof_loop.py",
    "test_aim_protocol.py",
    # 浏览器调参台(零窗口) + 相机/性能档公共库
    "color_blob_demo.py", "color_blob_stream.py", "ring_stream_ui.py", "monitor_ui.py",
    "digit_templates_ext.py",
    # 平台探测/性能 + 调优三件套(定位瓶颈用)
    "probe_platform.py", "probe_jetson.py", "setup_platform.sh",
    "bench_perf.sh", "bench_cam.sh",
    "prof_ui.py", "exp_speed.py", "exp_config.py",
    # 运维小脚本
    "kill_rsui.sh", "rsui_test.sh", "cbs_test.sh", "rdiag.sh",
    # 文档
    "README.md", "上位机指令对照表.md", "SSH命令大全.md",
    "deploy_to_pi.ps1", "deploy_to_jetson.ps1", "deploy_to_rdk.ps1", "pull_from_rdk.ps1"
)
# 圆环识别主库 + 三套回归(缺了 ring_stream_ui 的"跑三套回归"按钮会报错) + QR 推流页
$extras = @("ring_center_detect_v2.py", "qr_stream_demo.py", "test_rotation.py",
            "test_dashed.py", "test_stability.py")
$variants = @("aim_and_place_glare_filter.py", "aim_and_place_pi_lite.py")
# 校赛上位机文件：**自动列目录**（避免以后新增文件忘了加清单 —— 2026-09-16 就漏过 car.py/school_main.py）
# firmware 子目录是 Keil 用的，不进上位机目录（只列顶层 *.py / *.json）
# ★但**现场标定档案绝不从本地推**：调参台是在板子上跑、把档案存在**运行时的工作目录**里的；
#   如果本地也放一份同名文件，就会被 scp 到 `<RemoteDir>/校赛/` 下 —— 而主程序在 校赛/ 里跑、
#   load_calib 优先命中同目录 → **读到另一份、现场调参白费**
#   （2026-09-17 真踩：color_profile.json 在根目录与 校赛/ 各有一份，md5 不同，内容也不一样）。
$fieldFiles = @("color_profile.json", "ring_calib.json", "aim_offset.txt")
$schoolFiles = @(Get-ChildItem -Path (Join-Path $SCHOOL_DIR "*.py") -File | Select-Object -ExpandProperty Name)
$schoolFiles += @(Get-ChildItem -Path (Join-Path $SCHOOL_DIR "*.json") -File |
                  Select-Object -ExpandProperty Name |
                  Where-Object { $fieldFiles -notcontains $_ })

$pub = Join-Path $env:USERPROFILE ".ssh\rdk_deploy.pub"
$pubTxt = if (Test-Path $pub) { (Get-Content $pub -Raw).Trim() } else { "" }

Write-Host "==> 目标: ${User}@${Rdk}:${RemoteDir}" -ForegroundColor Cyan
Write-Host "==> 探测 RDK X5 在线状态 ..." -ForegroundColor Cyan
if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
ssh -o ConnectTimeout=8 -o BatchMode=yes $User@$Rdk "echo SSH_OK" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! RDK X5 $Rdk 不在线(ping 不通)。请确认:" -ForegroundColor Yellow
    Write-Host "    1) RDK X5 已上电并连到同一网络(和树莓派/Jetson 同一网段)"
    Write-Host "    2) IP 是否正确(可看路由器, 或 ssh rdk 试别的地址)"
    exit 1
}

$env:HOME = $env:USERPROFILE
ssh -o ConnectTimeout=8 -o BatchMode=yes $User@$Rdk "echo SSH_OK" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! SSH 未授权(publickey 被拒)。只需做一次:" -ForegroundColor Yellow
    Write-Host "   在 RDK X5 的终端里执行下面这条(整行粘贴, 含引号):" -ForegroundColor White
    Write-Host ""
    Write-Host ("   mkdir -p ~/.ssh && chmod 700 ~/.ssh && " +
                "echo '" + $pubTxt + "' >> ~/.ssh/authorized_keys && " +
                "chmod 600 ~/.ssh/authorized_keys") -ForegroundColor Green
    Write-Host ""
    Write-Host "   然后重跑本脚本即可(Windows 侧 ~/.ssh/config 已加好 rdk 别名)。" -ForegroundColor White
    exit 1
}
Write-Host "==> SSH OK, 准备远程目录 ..." -ForegroundColor Cyan
ssh $User@$Rdk "mkdir -p $RemoteDir/digit_custom $RemoteDir/ring_ui_out $RemoteDir/color_demo_out $RemoteDir/校赛" 2>&1 | Out-Null

function Send-One([string]$localPath, [string]$destDir) {
    if (-not (Test-Path -LiteralPath $localPath)) {
        Write-Host "   !! 缺少 $localPath (跳过)" -ForegroundColor Yellow
        return
    }
    $name = Split-Path -Leaf $localPath
    scp -q -o ConnectTimeout=15 $localPath "${User}@${Rdk}:${destDir}/${name}"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   !! scp 失败: $name" -ForegroundColor Red
    } else {
        Write-Host "   - $name" -ForegroundColor Green
    }
}

Write-Host "==> 传核心工具 + 文档 ($($tools.Count) 个) ..." -ForegroundColor Cyan
foreach ($f in $tools) { Send-One (Join-Path $DEP $f) $RemoteDir }

Write-Host "==> 传圆环识别库 + 回归脚本 ($($extras.Count) 个, 来自 _pdf_extract) ..." -ForegroundColor Cyan
foreach ($f in $extras) { Send-One (Join-Path $EXT $f) $RemoteDir }

Write-Host "==> 传视觉变体 ($($variants.Count) 个) ..." -ForegroundColor Cyan
foreach ($f in $variants) { Send-One (Join-Path $ROOT $f) $RemoteDir }

# ------------------------------------------------------------------
# ★现场改动保护(2026-09-16 事故: 部署把 RDK 上刚调好的参数覆盖了, 靠 VSCode 本地历史才救回)
#   受保护文件(默认 school_main.py)只要"远端内容 != 本地内容", 就说明板子上被现场改过:
#     ① 远端备份成 <名>.field_<Stamp>.bak   ② 本次跳过不覆盖(除非 -Force)
#   要把现场改动收进本地:  powershell -ExecutionPolicy Bypass -File pull_from_rdk.ps1 -Apply
# ------------------------------------------------------------------
$fieldDiffs = @()
foreach ($f in $Protect) {
    $remotePath = "$RemoteDir/校赛/$f"
    $localPath = Join-Path $SCHOOL_DIR $f
    if (-not (Test-Path -LiteralPath $localPath)) { continue }
    $remoteMd5 = (ssh $User@$Rdk "md5sum '$remotePath' 2>/dev/null") -split '\s+' | Where-Object { $_ } | Select-Object -First 1
    if (-not $remoteMd5) { continue }                       # 远端还没有这个文件 -> 正常上传
    $localMd5 = (Get-FileHash -LiteralPath $localPath -Algorithm MD5).Hash.ToLower()
    if ($remoteMd5 -eq $localMd5) { continue }
    if ($Force) {
        ssh $User@$Rdk "cp -a '$remotePath' '$remotePath.field_$Stamp.bak'" 2>&1 | Out-Null
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
    ssh $User@$Rdk "mkdir -p $RemoteDir/logic_v2" 2>&1 | Out-Null
    Get-ChildItem -Path (Join-Path $logicDir "*") -Include *.py, *.md -File -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { Send-One $_.FullName "$RemoteDir/logic_v2" }
}

Write-Host "==> 远程语法校验 ..." -ForegroundColor Cyan
ssh $User@$Rdk "cd $RemoteDir && python3 -m py_compile *.py && cd 校赛 && python3 -m py_compile *.py && echo 'PY_COMPILE_OK'"

if ($Link) {
    Write-Host "==> 更新 current 软链: ${StableDir}_current -> $RemoteDir" -ForegroundColor Cyan
    ssh $User@$Rdk "ln -sfn $RemoteDir ${StableDir}_current && ls -ld ${StableDir}_current" 2>&1 | Out-String | Write-Host
}

if (-not $NoProbe) {
    Write-Host "==> 平台/相机探测(约 $([int]$ProbeSeconds * 4) 秒; 期间别让别的程序占用相机) ..." -ForegroundColor Cyan
    ssh $User@$Rdk "cd $RemoteDir && timeout 120 python3 -u probe_platform.py --seconds $ProbeSeconds"
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  部署完成!  远程工程目录: $RemoteDir" -ForegroundColor Green
if ($Link) { Write-Host "  固定入口: ${StableDir}_current -> $RemoteDir" -ForegroundColor Gray }
Write-Host "  RDK X5 上的启动步骤(与树莓派/Jetson 一致):" -ForegroundColor Green
Write-Host "  0. 进目录:      cd $RemoteDir" -ForegroundColor White
Write-Host "  1. 圆环调参台:  python3 ring_stream_ui.py   --perf rdk --cam 1 --port 8091" -ForegroundColor White
Write-Host "  2. 颜色调参台:  python3 color_blob_stream.py --perf rdk --cam 1 --port 8090" -ForegroundColor White
Write-Host "  3. 瞄准放置:    python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8092" -ForegroundColor White
Write-Host "  4. 校赛主程序:  cd 校赛 && python3 test_school_local.py && python3 -u school_flow.py --dry-run" -ForegroundColor White
Write-Host "  RDK X5: OpenCV 4.11(不带 GStreamer) -> 不要加 --gst; 没相机时先 --no-cam/--image" -ForegroundColor Gray
Write-Host "  浏览器打开 http://${Rdk}:8091/  (RDK 上不开任何窗口, 纯网页面板)" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor Green
