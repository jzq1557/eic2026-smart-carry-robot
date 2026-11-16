# ============================================================
# deploy_to_jetson.ps1 —— 一键把上位机套件部署到 Jetson Orin Nano
#
# 用法(在 Windows PowerShell 运行; Jetson 须同一局域网且 ssh 可达):
#   powershell -ExecutionPolicy Bypass -File deploy_to_jetson.ps1
#   powershell -ExecutionPolicy Bypass -File deploy_to_jetson.ps1 -Stamped      # 目录带时间戳
#   powershell -ExecutionPolicy Bypass -File deploy_to_jetson.ps1 -WithLogic    # 连 v2 纯逻辑层一起传
#
# 目标目录: /home/jetson/VisualBot   (Jetson 默认 jetson 登录)
# 时间戳模式: -Stamped → /home/jetson/VisualBot_20260916_1224，并更新 VisualBot_current 软链
#
# 三台机器同一套代码: 树莓派 4B(~/vision_robot) / Jetson Orin Nano(~/VisualBot) / RDK X5(/root/VisualBot)
#
# [!] 本文件必须存成 UTF-8 with BOM: PowerShell 5.1 把无 BOM 的 UTF-8 当 GBK 读,
#     中文会变乱码并吃掉字符串引号 → "Unexpected token ')'" 这种莫名语法错误。
#
# Jetson 与另两台的差异:
#   · OpenCV 4.10 **带 GStreamer/CUDA** → 只有 CSI 排线摄像头才需要 --gst, UVC 不要加;
#   · 出厂没有 CSI 摄像头(实测 nvarguscamerasrc 报 No cameras available), 板载是 UVC;
#   · 性能档 auto → high(真机 34fps, 瓶颈反而在相机 14.9fps)。
# ============================================================
param(
    [string]$Jetson = "10.48.81.108",
    [string]$User = "jetson",
    [string]$RemoteDir = "/home/jetson/VisualBot",
    [switch]$WithLogic,
    [switch]$NoProbe,
    [switch]$Stamped,                                   # ★工程文件夹按时间命名: VisualBot_20260916_1224
    [string]$Stamp = (Get-Date -Format "yyyyMMdd_HHmm"),
    [switch]$NoLink,                                    # 不更新 current 软链
    [double]$ProbeSeconds = 2.0
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
    # 瞄准/标定/协议
    "aim_and_place.py", "calibrate_aim.py", "cam_diag_aim.py", "cam_probe.py",
    "find_cam.py", "smoke_calib.py", "tpl_time.py", "prof_loop.py",
    "test_aim_protocol.py",
    # 浏览器调参台(零窗口) + 相机/性能档公共库
    "color_blob_demo.py", "color_blob_stream.py", "ring_stream_ui.py", "monitor_ui.py",
    "digit_templates_ext.py",
    # 平台探测/性能 + 调优三件套(定位瓶颈用)
    "probe_platform.py", "probe_jetson.py", "setup_platform.sh", "setup_jetson.sh",
    "bench_perf.sh", "bench_cam.sh",
    "prof_ui.py", "exp_speed.py", "exp_config.py",
    # 运维小脚本
    "kill_rsui.sh", "rsui_test.sh", "cbs_test.sh", "rdiag.sh",
    # 文档
    "README.md", "上位机指令对照表.md", "SSH命令大全.md",
    "deploy_to_pi.ps1", "deploy_to_jetson.ps1", "deploy_to_rdk.ps1"
)
# 圆环识别主库 + 三套回归(缺了 ring_stream_ui 的"跑三套回归"按钮会报错) + QR 推流页
$extras = @("ring_center_detect_v2.py", "qr_stream_demo.py", "test_rotation.py",
            "test_dashed.py", "test_stability.py")
$variants = @("aim_and_place_glare_filter.py", "aim_and_place_pi_lite.py")
# 校赛上位机主程序（只传 .py/.json/.md；firmware 是 Keil 用的，不进上位机目录）
# 校赛上位机文件：**自动列目录**（避免以后新增文件忘了加清单 —— 2026-09-16 就漏过 car.py/school_main.py）
$schoolFiles = @(Get-ChildItem -Path (Join-Path $SCHOOL_DIR "*.py") -File | Select-Object -ExpandProperty Name)
$schoolFiles += @(Get-ChildItem -Path (Join-Path $SCHOOL_DIR "*.json") -File | Select-Object -ExpandProperty Name)

$pub = Join-Path $env:USERPROFILE ".ssh\jetson_deploy.pub"
$pubTxt = if (Test-Path $pub) { (Get-Content $pub -Raw).Trim() } else { "" }

Write-Host "==> 目标: ${User}@${Jetson}:${RemoteDir}" -ForegroundColor Cyan
Write-Host "==> 探测 Jetson 在线状态 ..." -ForegroundColor Cyan
if (-not (Test-Connection -ComputerName $Jetson -Count 1 -Quiet -ErrorAction SilentlyContinue)) {
    Write-Host "!! Jetson $Jetson 不在线(ping 不通)。请确认:" -ForegroundColor Yellow
    Write-Host "    1) Jetson 已上电并连到同一网络(和树莓派/RDK 同一网段)"
    Write-Host "    2) IP 是否正确(可用 VSCode Remote-SSH 里那条连接看主机名)"
    exit 1
}

$env:HOME = $env:USERPROFILE
ssh -o ConnectTimeout=8 -o BatchMode=yes $User@$Jetson "echo SSH_OK" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! SSH 未授权(publickey 被拒)。只需做一次:" -ForegroundColor Yellow
    Write-Host "   在 Jetson 的终端里执行下面这条(整行粘贴, 含引号):" -ForegroundColor White
    Write-Host ""
    Write-Host ("   mkdir -p ~/.ssh && chmod 700 ~/.ssh && " +
                "echo '" + $pubTxt + "' >> ~/.ssh/authorized_keys && " +
                "chmod 600 ~/.ssh/authorized_keys") -ForegroundColor Green
    Write-Host ""
    Write-Host "   然后重跑本脚本即可(Windows 侧 ~/.ssh/config 已加好 jetson 别名)。" -ForegroundColor White
    exit 1
}
Write-Host "==> SSH OK, 准备远程目录 ..." -ForegroundColor Cyan
ssh $User@$Jetson "mkdir -p $RemoteDir/digit_custom $RemoteDir/ring_ui_out $RemoteDir/color_demo_out $RemoteDir/校赛" 2>&1 | Out-Null

function Send-One([string]$localPath, [string]$destDir) {
    if (-not (Test-Path -LiteralPath $localPath)) {
        Write-Host "   !! 缺少 $localPath (跳过)" -ForegroundColor Yellow
        return
    }
    $name = Split-Path -Leaf $localPath
    scp -q -o ConnectTimeout=15 $localPath "${User}@${Jetson}:${destDir}/${name}"
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

Write-Host "==> 传校赛主程序 ($($schoolFiles.Count) 个 → $RemoteDir/校赛) ..." -ForegroundColor Cyan
foreach ($f in $schoolFiles) { Send-One (Join-Path $SCHOOL_DIR $f) "$RemoteDir/校赛" }

if ($WithLogic) {
    $logicDir = Join-Path $ROOT "car_code上位机"
    Write-Host "==> 传 v2 纯逻辑层 (整机流程/任务码/串口执行) ..." -ForegroundColor Cyan
    ssh $User@$Jetson "mkdir -p $RemoteDir/logic_v2" 2>&1 | Out-Null
    Get-ChildItem -Path (Join-Path $logicDir "*") -Include *.py, *.md -File -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { Send-One $_.FullName "$RemoteDir/logic_v2" }
}

Write-Host "==> 远程语法校验 ..." -ForegroundColor Cyan
ssh $User@$Jetson "cd $RemoteDir && python3 -m py_compile ring_stream_ui.py color_blob_stream.py color_blob_demo.py ring_center_detect_v2.py digit_templates_ext.py aim_and_place.py probe_platform.py && cd 校赛 && python3 -m py_compile school_flow.py aim.py vision.py lower_api.py ledger.py qr_scan.py school_config.py && echo 'PY_COMPILE_OK'"

if (-not $NoLink) {
    Write-Host "==> 更新 current 软链: ${StableDir}_current -> $RemoteDir" -ForegroundColor Cyan
    ssh $User@$Jetson "ln -sfn $RemoteDir ${StableDir}_current && ls -ld ${StableDir}_current" 2>&1 | Out-String | Write-Host
}

if (-not $NoProbe) {
    Write-Host "==> 平台/相机探测(约 $([int]$ProbeSeconds * 4) 秒; 期间别让别的程序占用相机) ..." -ForegroundColor Cyan
    ssh $User@$Jetson "cd $RemoteDir && timeout 120 python3 -u probe_platform.py --seconds $ProbeSeconds"
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  部署完成!  远程工程目录: $RemoteDir" -ForegroundColor Green
if (-not $NoLink) { Write-Host "  固定入口: ${StableDir}_current -> $RemoteDir" -ForegroundColor Gray }
Write-Host "  Jetson 上的启动步骤(与树莓派/RDK 一致):" -ForegroundColor Green
Write-Host "  0. 进目录:      cd $RemoteDir" -ForegroundColor White
Write-Host "  1. 圆环调参台:  python3 ring_stream_ui.py   --perf auto --cam 0 --port 8091" -ForegroundColor White
Write-Host "  2. 颜色调参台:  python3 color_blob_stream.py --perf auto --cam 0 --port 8090" -ForegroundColor White
Write-Host "  3. 瞄准放置:    python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8092" -ForegroundColor White
Write-Host "  4. 校赛主程序:  cd 校赛 && python3 test_school_local.py && python3 -u school_flow.py --dry-run" -ForegroundColor White
Write-Host "  Jetson: 只有 CSI 才加 --gst(本机板载是 UVC, 不要加); 性能档 auto 会自动选 high" -ForegroundColor Gray
Write-Host "  浏览器打开 http://${Jetson}:8091/  (Jetson 上不开任何窗口, 纯网页面板)" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor Green
