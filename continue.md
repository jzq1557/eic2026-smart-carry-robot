# continue.md —— 上位机/下位机 工程交接文档（历史附录）

> ⚠️ **已被根目录 `README.md` 整合取代**（2026-09-12）：新接手者请先读 `README.md`（含赛项解读、上下位机全貌、坑表、现场手册）。
> 本文保留为**过程细节与 09-05 之前的原始记录**，需要追溯历史时再查。

> 交接对象：下一个接手的 agent
> 交接人：上一轮完成视觉上位机开发的 agent；2026-09-05 由接手 agent 复核增补（含 09-02~09-05 的瞄准/放料新工作线）
> 最后更新：2026-09-05
> **树莓派当前地址：`10.169.170.145`（旧地址 192.168.3.30 已不可达，勿再使用；本机当前无 ~/.ssh/config 与密钥、Pi 不在线，远端验证需现场）**
> **开发流程教学：先读 `lecture.md`（SSH 开发全流程、测试体系、十大坑、命令速查）再动手**
> ⚠️ **最新工作线速览（09-02→09-05）**：瞄准/放料视觉闭环 `deploy_pi/aim_and_place.py`（树莓派）+ 下位机固件 `26090501TEST`（CLEAN 重构，TTL 四路闭环电机 + 协议 0x01~0x16）——两端的"方案/文档"已就绪，**下位机 hex 尚未按最终源码重新编译**，详见 §8。

---

## 0. 一分钟速览（先读这个）

| 项 | 位置 | 状态 |
|---|---|---|
| 赛题规则（智能搬运） | `E:\工程文档\27工创赛\智能搬运赛道要求研读笔记.md` + `_pdf_extract\附件2-1命题与运行.txt`、`附件2-2评分与规则.txt` | 已研读 |
| 旧上位机分析 | `E:\工程文档\27工创赛\整机程序设计\上位机程序分析.md` | 已分析，含高风险项 |
| **圆环检测+数字识别 demo** | 树莓派 `/home/justin/vision_robot/ring_center_detect_v2.py` | ✅ 全测试通过 |
| **二维码识别推流 demo** | 树莓派 `/home/justin/vision_robot/qr_stream_demo.py` | ✅ 全测试通过 |
| 任务码二维码生成器（网页） | `E:\工程文档\27工创赛\任务码二维码生成器.html` | ✅ 单文件离线可用 |
| 老师车部署（下位机+上位机） | `整机程序设计\STM32F103ZET6_下位机_原硬件底盘命令版`、`RaspberryPi4B_上位机_底盘手动控制` | ⚠️ 需适配 base轴舵机（历史线） |
| **瞄准/放料视觉闭环（当前主线）** | `整机程序设计\deploy_pi\` → 树莓派 `~/vision_robot/`（aim_and_place.py / calibrate_aim.py / find_cam / smoke / diag + README + 上位机指令对照表） | ✅ **2026-09-05 晚已部署并验证**：md5 一致、smoke OK、一键回归全绿；K 系数/CAM_GRIP_OFFSET 待现场实测 |
| **下位机固件（当前主线）** | `整机程序设计\26090501TEST\`（CLEAN 重构：car_config/chassis/claw/ramp/TTLctrl/Emm_V5/protocol_and_main，协议 0x01~0x16） | ⚠️ **需 Rebuild 生成 hex 再烧录**（当前 OBJ 空；旧 hex 早于 TTLctrl 手调改动） |
| 高光鲁棒性实验 | `_pdf_extract\exp_highlight_ring.py`、`bench_pipeline.py` | ⏸ 暂停：本地无 cv2 无法跑视觉、也暂无实拍输入图；待现场实拍图复跑（脚本已改为自动找 A4 靶标图或传参） |
| VSCode 项目配置 | `E:\工程文档\27工创赛\.vscode\`（settings/extensions/launch/tasks） | ✅ 已就绪（tasks 已更新新 IP + deploy_pi 任务） |

---

## 1. 环境与访问

### 1.1 树莓派（唯一真机目标）
- 地址：**`10.169.170.145`**，用户 `justin`，密钥 `~/.ssh/pi_deploy`
- 本地 `~/.ssh/config` **已更新**：`Host 10.169.170.145`（含 User/IdentityFile）；`192.168.3.30` 条目保留但已不可达；`RasberryPi`(10.204.121.145) 也连不上
- 验证：`ssh 10.169.170.145 "hostname"` → RaspberryPi
- 代码目录：`/home/justin/vision_robot/`
- 推流地址：`http://10.169.170.145:8080/`（两个 demo 默认 8080 端口）
- ⚠️ 2026-09-05 接手检查：**本机（Justin 用户）`~/.ssh` 下没有 config/pi_deploy 密钥，Pi 当前也不在线**（旧说明是上一环境/账号的配置）。换机器/换账号后需重建 `Host 10.169.170.145` 的 User/IdentityFile 条目或重新 ssh-copy-id，否则无法部署/验证。
- ✅ **2026-09-05 晚 已恢复在线并部署成功**：Pi 同网在线；本机已生成 `~/.ssh\pi_deploy`(ed25519)、写 `~/.ssh/config`（Host 10.169.170.145 / User justin / IdentityFile → pi_deploy）并加入 Pi authorized_keys，**免密可用**（验证 hostname=RaspberryPi）。deploy_pi 5 文件已部署且与本地 **md5 全一致**；smoke_calib 冒烟 OK（video0=1280x720，圆环 5/5 检出 SMOKE_OK）；deploy_to_pi.ps1 已修 `$ErrorActionPreference=Continue`（原 Stop 会被 ssh 的无害 locale 警告中断部署）。

### 1.2 本地工作副本（与树莓派 md5 一致）
- `E:\工程文档\27工创赛\_pdf_extract\ring_center_detect_v2.py`（md5 `db59dc05...`）
- `E:\工程文档\27工创赛\_pdf_extract\qr_stream_demo.py`（md5 `611f6cc7...`）
- 存档副本：`E:\工程文档\27工创赛\整机程序设计\ring_center_detect_v2_digit版.py`、`qr_stream_demo.py`
- **改文件流程**：本地 `_pdf_extract` 改 → `scp` 到树莓派 → 树莓派跑测试。
  ⚠️ 2026-09-05 修正：本机其实有 **Anaconda Python 3.13**（`D:\programs\anaconda\python.exe`，numpy 有、**cv2/pyserial 无**，pip 装 opencv 网络不通）。
  → 视觉/串口仍以 Pi 为准；**纯协议逻辑可在本机自测**：`python deploy_pi\test_aim_protocol.py`（帧格式/ACK/命令字回归，已全绿）。
  → 已把回归挂进 `.vscode\tasks.json`：终端→运行任务→「回归: 协议层」「回归: 官方任务码解析」「工具: 图片主色调探针 probe_palette.py」（PIL 在主色调探针用，中文路径 OK）。

### 1.3 网络注意事项
- `code --install-extension` 连微软插件市场会 **ETIMEDOUT**（网络被拦）；VSCode 里装插件也可能受影响 → 插件走手动安装，必要时 VSIX 离线装
- 树莓派 SSH 目前仅新地址可达；现场如需换网，改 `~/.ssh/config` 的 HostName 即可

---

## 2. 树莓派 vision_robot 内容清单（全部现状）

```
/home/justin/vision_robot/
├── ring_center_detect_v2.py      # ★圆环检测+环心数字识别推流 demo（主交付）
├── qr_stream_demo.py             # ★二维码识别推流 demo（主交付）
├── test_rotation.py              # 回归：1/2/3 × 12 角度，36 项
├── test_dashed.py                # 回归：虚线环场景，36 项
├── test_stability.py             # 回归：滑动窗口众数 5 场景
├── gen_test_qr.py                # 生成测试二维码 test_qr.png
├── test_qr.png                   # 测试码（内容 156+123+516+231）
├── ring_center_simple.py         # 早期极简版（检测思路来源）
├── ring_center_detect_v2.py.bak / .before_digit.bak   # 旧版备份
├── vision_design.md              # 早期总体设计文档（架构已过时，仅参考）
├── vision_skeleton.py            # 早期骨架（未用）
├── cam_diag.py / capture_check.sh / test_stream.sh     # 相机诊断/冒烟脚本
```

### 2.1 ring_center_detect_v2.py（圆环 + 数字）
**功能**：粗圆环带检测（灰度→自适应二值→闭运算→最大连通域→最小外接圆）→ 环心数字识别（1/2/3，任意 360° 旋转）→ 滑动窗口众数输出 → MJPEG 推流。

**CALIB 关键参数（改这里，都在文件顶部）**：
| 参数 | 值 | 含义 |
|---|---|---|
| width/height | 1280×720 | 分辨率（640×480 更快） |
| thr_c | 5 | 灵敏度（越小越灵敏） |
| center_gate_r | 200 | 环心必须在画面中心半径 200px 内才识别，0=关 |
| cluster_eps | ~~32~~ | **早期霍夫版参数,现代码已不用(检测=最大连通域,无霍夫),勿再引入** |
| digit_votes | 20 | 滑动窗口长度（众数输出） |
| digit_thr | 0.42 | NCC 阈值（无数字基线 0.00；斜置细'1'≈0.45~0.50） |
| digit_inner_frac | 0.80 | 数字裁剪 = 2×φ1×0.8（严格在最小环内，虚线环不进裁剪区） |
| digit_rot_step | 5 | 旋转模板步长 |
| jump_px | 120 | 环心跳变判定（换环清窗） |
| proc_scale | 0.5 | 检测用半分辨率，坐标自动换算 |
| **rotate_ccw** | **True** | **摄像头画面逆时针旋转90°（默认开）；输出坐标=旋转后画面坐标** |

**检测核心**：`find_center`（粗环带）→ `find_inner_radius`（**白洞法**：取贴近环心半径最小的洞=φ1；φ1 虚线时退到 φ2 的洞）→ `recognize_digit`（中心区字形 + 旋转模板库 NCC）→ 窗口众数。

**测试命令**：`python3 test_rotation.py` / `test_dashed.py` / `test_stability.py`（当前全过）。
**运行**：`python3 ring_center_detect_v2.py --stream`（浏览器 `http://10.169.170.145:8080/`）。

### 2.2 qr_stream_demo.py（二维码识别）
**功能**：OpenCV `QRCodeDetector.detectAndDecodeMulti`（支持多码），绿框+黄字标注，控制台打印识别内容，MJPEG 推流。

**CFG**：`qr_scale 0.5`（半分辨率，实测 200px 以上码 100% 正确）、`detect_every 3`（隔帧解码，结果复用）。

**性能优化（重要，已实测）**：
- 摄像头 720p 原生上限约 **17fps**（read 固定 ~60ms，所有分辨率都是）——帧率天花板；
- **后台采集线程**：把 `cap.read()` 阻塞移出主循环（关键优化）；
- 就地标注省 copy、JPEG q80、半分辨率+隔帧解码；
- 实测：**5.7fps → 11.5~13.3fps**（顶到摄像头上限）。
- 兜底：半分辨率没找到时每 24 帧全分辨率重试（防小码/远码）。

**运行**：`python3 qr_stream_demo.py --stream`。

### 2.3 测试脚本说明
- `test_rotation.py`：合成靶标（按 CALIB 精确画 6 环）1/2/3 × 12 角度 → 36/36 ✅
- `test_dashed.py`：虚线环（φ2φ5 虚线 / φ1 虚线）→ φ1 估计正确 + 36/36 ✅
- `test_stability.py`：窗口众数（滚动/翻转/丢失保持/超时清窗/跳变清窗）5 场景 ✅
- `gen_test_qr.py`：用 cv2.QRCodeEncoder 生成 test_qr.png（注：`enc.encode()` 直接返回 25 行矩阵，用 `np.stack`）

### 2.4 本轮会话修复（2026-09-01，已同步 Pi 并全测试通过）
- **修复：单张图片调试模式在 SSH 无显示下无限循环**（两个 demo 都有）。根因：图片模式主循环 `while True`，`--no-show` 降级后 `if not show: continue` 永不退出。修复：图片模式处理完打印 `[result]` 后直接 break 退出。
- **修复：图片模式位置门误伤**。`ring_center_detect_v2.py` 图片模式下自动 `center_gate_r=0`（离线整图环可在画面任意位置，A4 靶标环心离画面中心 ~800px 会被 200px 门拒掉）。
- **修复：qr 图片模式首帧不解码**。`detect_every=3` 时第 1 帧 `frame_no=1` 不解码，图片模式只跑 1 帧 → 必然"未识别"。修复：图片模式强制 `detect_every=1`。
- **新增：摄像头画面逆时针旋转90°（rotate_ccw）**。`CALIB/CFG["rotate_ccw"]=True`（默认开，可改 False 关闭），读帧后立即 `cv.rotate(ROTATE_90_COUNTERCLOCKWISE)`，**输出坐标（环心/半径/QR角点）均为旋转后画面坐标，无需手工映射**。已验证：环心 `(1157,2597)→(2597,1321)` 符合逆时针90°映射 `(x,y)→(y,W-1-x)`，半径不变，QR 角点 max_err=0.00px。验证脚本 `verify_rotate.py` 全过。
- **新增真实图片验证**（合成靶标之外的实测）：`工创赛标靶A4(12).png`→digit=2(1.00)、`A4(13)`→digit=3(1.00)、`test_qr.png`→`156+123+516+231`，全部识别正确。
- **新增一键验证脚本**：`run_all_tests.py`（语法+三套回归+真实标靶图+二维码，scp 到 Pi 后 `python3 -u run_all_tests.py` 一条命令跑完）。另有 `diag_real_images.py`（排查真实图识别失败的诊断脚本）、`verify_rotate.py`（旋转坐标映射验证）。
- **tasks_pi.json 已部署**：`/home/justin/vision_robot/.vscode/tasks.json`（含 6 个一键任务：2 个推流 demo + 3 个回归 + 生成测试码）。
- 本地 `_pdf_extract` 与 Pi 全部 6 个文件 md5 一致（`db59dc05...`/`611f6cc7...` 等），推流实测：ring 12.5fps、qr 13.3fps，HTTP 均 200。

### 2.5 摄像头对焦结论（2026-09-01 实测）
- **摄像头：Microdia LRCP G720P（USB ID 0c45:1915），硬件定焦，无自动对焦马达**，软件无法调焦。证据：
  1. `v4l2-ctl --list-ctrls`：15 个用户控件无 focus/zoom/pan/tilt；
  2. OpenCV `CAP_PROP_FOCUS/AUTOFOCUS` 返回 `-1.0`，set 失败；
  3. UVC 描述符 Camera Sensor 终端仅声明曝光控件（`bmControls 0x0000000e`），无 Focus bit（dmesg 的 "control 12 on unit 2" 是 Processing Unit 的**白平衡自动**，与对焦无关）。
- **若需对焦到 ~20cm 地面 → 物理拧镜头**（此类定焦摄像头镜头多为螺纹旋入可手动旋转）。用新增工具 `focus_assist.py` 辅助：`python3 focus_assist.py --stream 8090`，浏览器看实时画面+清晰度分数（拉普拉斯方差，越大越清晰），拧镜头使目标区域分数最大化。实测当前焦点在下半中央（夹爪/近物，分数 62~69）而非远处（中央区仅 28~30），与用户观察一致。
- 诊断脚本：`focus_scan.py`（对焦扫描，此摄像头无 focus 控件已确认无效）、`focus_diag.py`（对焦能力终诊）。
- ⚠️ 注意：任何进程开着摄像头时其他程序打不开（VideoCapture 报 "can't open camera by index"），先 `ps aux | grep python3` 清理残留 demo 进程。

---

## 3. 本地交付物

### 3.1 任务码二维码生成器.html
- 路径：`E:\工程文档\27工创赛\任务码二维码生成器.html`
- 单文件、离线可用（qrcode-generator 库已内嵌，无外部依赖）
- 功能：随机生成 / 四组三位数输入（"+"写死）/ 校验提示 / 生成刷新 / 720×720 二维码居中
- **校验规则**（赛题附件2-1）：第 1、3 组=颜色编号 1~6 且不重复；第 2、4 组=位置 1~3 且不重复；每组恰好 3 位数字
- 测试：`E:\工程文档\27工创赛\_pdf_extract\test_qr_tool.js`（Node 运行，18 项校验全过）
- 注意：文件无标题（按用户要求删过），中心化渲染用"canvas 按二维码实际尺寸+CSS 居中"方案

### 3.2 文档
- `智能搬运赛道要求研读笔记.md`：赛题全要素（尺寸/电源/显示/场地/物料/任务码/评分）
- `整机程序设计\上位机程序分析.md`：旧上位机（car_code上位机）分析，含高危问题清单（任务码 4 组解析缺失、颜色编号不匹配、无超时等）——**若继续做全流程搬运，这些是必改项**

### 3.3 VSCode 项目配置（`E:\工程文档\27工创赛\.vscode\`）
- `settings.json`：编辑器/编码/终端/Python/flake8/autopep8/Markdown（项目级，与其他项目不冲突）
- `extensions.json`：推荐扩展清单
- `launch.json`：Python/Node 调试当前文件
- `tasks.json`：**现共 12 个任务**——打开工具/文档、scp 同步 demo 到树莓派(新 IP)、deploy_pi 一键部署、以及本机回归一键跑：协议层 / 任务码解析 / 统计 / FlowRunner / cmd_pack / serial_exec / probe_palette
- **Profile（配置文件）方案已撤销**：创建了又删了（`storage.json` 的 `userDataProfiles` 已还原为只剩 STM32）。原因：CLI 装扩展 ETIMEDOUT。教训：profile 注册在 `%APPDATA%\Code\User\globalStorage\storage.json` 的 `userDataProfiles`；profile 目录里**没有 profile.json**；`code --profile "名"` 在 VSCode 已运行时不会真正创建。

---

## 4. 老师车部署（新增交付，未完成适配）

两个新目录（已研读，未改动）：
- `整机程序设计\STM32F103ZET6_下位机_原硬件底盘命令版\`（Keil 工程，CLEAN 重写版）
- `整机程序设计\RaspberryPi4B_上位机_底盘手动控制\`（树莓派 Python）

### 4.1 协议（README 已写明，双方一致）
- 帧：`0xFF + 8字节payload + 0xFE`；回执 `FF 01 FE`；握手 `FF FD 00...FE`
- 串口：USART2 重映射 **PD5(TX2)/PD6(RX2)**，115200 8N1
- 命令：0x01=前后移(buf[1] int8 cm)、0x03=旋转(buf[3] int8 度)、0x02/0x05/0x06=Y/mm、0x04/0x0C=二维码/升降（**统一大端**，树莓派端已同步）、0x07~0x0E=抓放/升降/复合/收尾
- 工程已确认包含 CLEAN 文件、排除旧 contral；烧录前 **Rebuild 生成 OBJ/Template.hex**（旧 hex 已删）

### 4.2 老师车硬件 vs 代码兼容性（重点）
| 老师车 | 代码 | 结论 |
|---|---|---|
| 麦克纳姆轮×4 | chassis.c 就是麦轮运动学（方向表） | ✅ 兼容，需核对 MOTOR3/4/7/8 引脚（DIR: PG7/PG8/PA8/PC1，轮2/3 反接 invert） |
| base轴 舵机 | LIFT 是步进（PA4+MOTOR1，LIFT_* 步数表） | ❌ **需改固件**：步进→舵机（car_config.h 高度表改角度表、claw.c Lift_* 改舵机写角度、删 axis_lift、0x0B/0x0C/0x0D 同步改） |
| 供料盘 步进 | TRAY/MOTOR5 步进（TRAY_STEPS_120DEG=1067） | ✅ 兼容，核对引脚/减速比 |
| 爪子/腕 舵机 | SERVO2/3（PA2/PA3） | ✅ 概念兼容 |
| 板型 | STM32F103ZET6 | ⚠️ 确认老师板是否同款/PD5/PD6 可用 |

### 4.3 部署步骤（详见随包 README）
- 下位机：Keil5 打开 `STM32_lower_car2/USER/Template.uvprojx` → Rebuild（0 Error）→ ST-Link/J-Link 烧 `OBJ/Template.hex`
- 上位机：`scp -r RaspberryPi_upper justin@10.169.170.145:~/` → `pip3 install pyserial` → `python3 manual_chassis_control.py --port /dev/ttyUSB0`（W/S/A/D 手动控制）
- 接线：TTL TX→PD6(RX2)、RX→PD5(TX2)、GND→GND

### 4.4 待确认（需要问用户/老师）
- "base轴"具体指哪个轴（升降台 or 基座旋转）？舵机型号（180°/270°）、脉宽范围、机械连接
- 老师车驱动板 PULSE/DIR 引脚、麦轮直径、供料盘减速比
- 老师板 STM32 串口引脚是否 PD5/PD6

---

## 5. 已知坑与注意事项（血泪教训，务必阅读）

1. **`cluster_eps` 必须 ≥ 32**：霍夫在同心圆上的圆心散布 10~25px，8px 会导致永远检测失败（本会话真实踩坑）。
2. **旧 `Detection.py` 的 `RGB2HSV`** 是通道互换的"错误用法"，但阈值是配合它调的——**不要"顺手"改成 BGR2HSV**，会全失效。
3. **任务码 4 组 vs 旧上位机 2 组**：旧 `car_code上位机/my_move.py` 只解析 `XXX+YYY`（`get_taskinfo` 的 `task_info[4:]` 遇"+"会崩）；颜色编号是 `[0,red,green,blue]`（1红2绿3蓝），与官方 6 色编号（红1黄2蓝3绿4黑5浅蓝6）不一致——全流程若沿用旧上位机必须改。
4. **pkill 自匹配**：`pkill -f 'xxx'` 会杀掉命令行里含该串的 bash 包装进程（含自己）——用 PID 文件或 `timeout` 代替。
5. **PowerShell 引号地狱**：ssh 远程执行含引号/heredoc 的命令极易被本机 PowerShell 转义搞坏——**写脚本文件 scp 过去再跑**，不要内联。
6. **`$HOME` 陷阱**（检查过，本会话无此问题）：PowerShell 变量不区分大小写，`$home` 是只读自动变量，禁止用作变量名。
7. **.ps1 中文**：Windows PowerShell 5.1 需要 **UTF-8 带 BOM**。
8. **老 MoveControl.py**：`serial.write(list)` 在 Python3 要改 `bytes(list)`（新包已修）。
9. **摄像头 720p 只有 ~17fps**：所有分辨率 read 都 ~60ms，别再指望降分辨率提速；提速靠后台采集线程+半分辨率解码。
10. **串口 0x04/0x0C 字节序**：固件已统一大端，上位机 `get_int16_be` 已同步——两处都要一致，别只改一边。

---

## 6. 下一步建议（接手后按优先级）【历史：已被 §10 取代，保留仅作溯源】

1. **老师车适配**：确认 4.4 的信息 → 改"升降台步进→舵机"适配固件（改动集中在 `car_config.h`+`claw.c`+`protocol_and_main.c`），并把上位机相关命令同步。
2. ~~**Pi 侧 tasks.json 部署**~~：**✅ 已完成**（2026-09-01，`/home/justin/vision_robot/.vscode/tasks.json`）。
3. **现场标定**：ring demo 的 `digit_thr`、`center_gate_r` 需按现场板子实测微调；若现场数字是印刷体（黑体/Arial），把现场字体加进 `_digit_templates` 模板库。**离线验证可用**：`python3 ring_center_detect_v2.py 现场照片.jpg`（已修好：处理完自动退出并打印 `[result]`）。可先用手持板子拍几张真实照片离线测，再微调参数。
4. **全流程搬运**（若接手）：旧 `car_code上位机` 的高危问题（任务码 4 组、6 色编号、超时机制）是必修项，见 `上位机程序分析.md` §8。
5. **文档同步**：`vision_design.md` 是早期架构，与实际实现（简单环带+白洞φ1+窗口众数）已不符，如继续使用建议重写。

---

## 7. 关键命令速查

```bash
# SSH
ssh 10.169.170.145

# 同步本地工作副本到树莓派
scp E:\工程文档\27工创赛\_pdf_extract\ring_center_detect_v2.py E:\工程文档\27工创赛\_pdf_extract\qr_stream_demo.py justin@10.169.170.145:/home/justin/vision_robot/

# 树莓派上跑测试（每次改动后必跑）
cd /home/justin/vision_robot
python3 test_rotation.py && python3 test_dashed.py && python3 test_stability.py

# 运行 demo（推流 8080）
python3 ring_center_detect_v2.py --stream    # 浏览器 http://10.169.170.145:8080/
python3 qr_stream_demo.py --stream

# 离线图片调试(已修复: 处理完自动退出, 打印 [result])
python3 ring_center_detect_v2.py 图片.jpg
python3 qr_stream_demo.py 图片.jpg

# 一键全验证(语法+回归+真实图, 需先 scp run_all_tests.py)
python3 run_all_tests.py

# 对焦辅助(物理拧镜头时看清晰度分数, 浏览器 http://<IP>:8090/)
python3 focus_assist.py --stream 8090
```

---

## 8. 新工作线（09-02→09-05）：瞄准/放料闭环 与 下位机 26090501TEST

### 8.1 分工与机械理解
- 当前真机 = 4×75mm 全向轮底盘 + base 旋转轴(交叉滚子轴承) + 龙门架 V2.0(镜像双轴升降) + 2040 伸缩臂滑台 + 齿轮夹爪(MG995×2) + 垂直下视摄像头 + 车载物架/载物台（结构来自整车机械设计，轴名以固件注释为准）。
- 运动链与驱动：**id1=伸缩滑台、id2/3=龙门架(镜像互反)、id4=base 轴** —— 4 路 Emm_V5.0 闭环电机，走 **USART3 PC10(TX)/PC11(RX) 115200**，地址 1..4（0=广播触发同步）；3200 脉冲/圈（200 整步 × 16 细分）；vel=RPM、dir 0=CW/1=CCW；acc 0-255。
- STM32 ↔ 树莓派通信用 **USART1 = PA9(TX)/PA10(RX)，115200**；帧 `0xFF + 8B + 0xFE`；每条命令执行完回执 `0xFF 0x01 0xFE`（0x10~0x12 非阻塞即时回执，0x13/0x16 阻塞动作组完成后才回执）。
- 瞄准原理（aim_and_place.py v4）：垂直相机看圆环 → 分轴 PID 闭环：阶段A 只用 base(id4) 收敛画面 x → 阶段B 只用伸缩(id1) 收敛画面 y → X2 二次复校 base-x；收敛后把"观察位→靶心"净偏移(base/arm 脉冲,带符号) 写入 `~/vision_robot/aim_offset.txt`。
- 全流程 `--mission`：瞄准记录偏移 → 归位(撤销偏移回观察位) → 0x16 去车载物架夹料(下位机动作组,结束须回观察位) → 回放偏移到靶心 → 0x13 放料(下放-开爪-抬升)。
- 方向约定（车上实测前参考）：dir0=正转；base dir0 → 画面右移；伸缩 dir0 → 画面下移；09-03 实测 K_PX2STEP_BASE=0.2593、K_PX2STEP_ARM=0.0413 px/脉冲。

### 8.2 部署包 deploy_pi（`整机程序设计\deploy_pi\`）
一键部署：`powershell -ExecutionPolicy Bypass -File deploy_to_pi.ps1 [-Pi 新IP]`
- 内容：aim_and_place.py / calibrate_aim.py / find_cam.py / smoke_calib.py / cam_diag_aim.py → Pi `~/vision_robot/`（**不覆盖**原版 ring/qr/test）。
- 用法/模式参数/指令对照：`deploy_pi\README.md` + `上位机指令对照表.md`（09-05 已同步）。
- 现场顺序：机械/相机位置与上次不同 → 先 calibrate_aim 重标 K → smoke 冒烟 → aim（--stream 看画面）→ 分步/全流程。

### 8.3 下位机最新固件 26090501TEST
- Keil 工程：`整机程序设计\26090501TEST\USER\26090501TEST.uvprojx`（STM32F103ZE，CreateHexFile=1）。
- 源码 = CLEAN 重构：car_config.h(全车数字仓库) / chassis / claw / ramp / TTLctrl / Emm_V5 / protocol_and_main.c(内含 main)。
- **⚠️ OBJ 为空、尚未编译。** `26090201TEST\OBJ\26090201TEST.hex`（09-03 14:51 编译，0 Error 1 Warning）与当前源码同构但**早于 TTLctrl.c 09-03 16:11 的 0x16 夹料组手调改动**——**烧录前必须 Rebuild 26090501TEST**。
- 0x16 动作组参数（3400/1800/6300/800…）为占位手调值，上车实测后定稿（注释已声明）。
- 命令字全集见 car_config.h `CarCommand`：0x01~0x0D（底盘/QR/抓放/升降/复合）+ 0x10~0x16（TTL 电机/动作组）；16/32 位参数统一大端。
- ✅ 2026-09-05 接手静态审计（下一次 Rebuild 前的风险预检）：参与编译的文件集内 **main 唯一**（仅 CLEAN\protocol_and_main.c；USER\main.c 未挂进工程）、中断处理**无重名**（TIM2/3/4→ramp.c、TIM6→time_count.c、USART1/2→uart.c、USART3→TTLctrl.c，motor.c 已无残留）、HARDWARE\CONTROL* 旧模块已移出、CLEAN 目录已含在 IncludePath —— 源码清单层面无重复符号风险，Rebuild 若报错应只是头文件胶水级。

### 8.4 接手一致性核对结论（09-05）
帧头尾/ACK/命令字/大端字节序/USART1 PA9-PA10 引脚在两端口径全部一致；`aim_and_place.py` ↔ `car_config.h` 命令一一对应（0x10→motor1、0x11→motor23、0x12→motor4、0x13→place、0x14→grip、0x15→grip_angle、0x16→grab）。剩余现场不确定项：旋转坐标空间 CALIB["rotate_ccw"] 是否开、K 系数、CAM_GRIP_OFFSET、动作组脉冲数。

### 8.5 老师车部署包通读增补（2026-09-05 子代理核读，归属历史线/老师车）
- **关系判定**：老师车部署包（`STM32F103ZET6_下位机_原硬件底盘命令版` + `RaspberryPi4B_上位机_底盘手动控制`）与当前 26090501TEST 是**同一项目车体、先后两代的机构换型**，不是两台无关车：固件 CLEAN/ramp/motor/uart/car_config 同源（新 main 仍初始化 MOTOR3/4/7/8、MOTOR1、Servo2/3、MOTOR5 并保留 0x01~0x0D 旧命令）；目录日期连续（07-17 car2_clean → 08-22~31 老师包 → 09-01/03/05 2609xx01TEST 迭代）。差异在执行机构：旧=立柱丝杆升降+腕关节二态+车上转盘（转盘抓-码垛）；新=Emm_V5 闭环（id1伸缩/id2·3龙门镜像/id4base）+视觉瞄准靶心放料。
- **串口双通路**：USART1 PA9(TX)/PA10(RX)（板上 USB）与 USART2 重映射 PD5(TX)/PD6(RX)（扩展座）共用收包状态机、ACK 双口都发；0x04 二维码=内部 OLED 字符串连发×5，不产生串口文本；u2_printf 已置空防污染 ACK。
- **字节序/ACK**：v16+ 全大端（0x0C 曾小端、已两端修，Python 用 get_int16_be）；固件对**未识别命令也照常回 ACK**——两端别把"有 ACK"当成"动作真的做了"。
- **老师车包内值得注意的坑**（适配它时必读）：① 舵机换算已重标定（1us/格，500~2500us，270°舵机），旧码"开=0/闭=95"在新表下语义变成"开=210/闭=120"，旧角度表配新固件会过冲打滑；② 丝杆 8→6mm 高度表重标（64→85 步/mm、LIFT_TOP 16000、LIFT_CAMERA 12200、LIFT_BLOCK_GET 12000）；③ 升降极性 PA4 **0=上升**（旧固件曾写反，接错会往底顶）；④ `LIFT_PUT_LAYER1=3000` 是台架临时值，落地跑真实场地前须改回 0；⑤ 上电"盲升归顶"约 16200 步无反馈靠机械限位，开机前必须清空上方；⑥ 麦轮 78mm / 130 步/cm / 51 步/°，int8 限幅 ±127；⑦ 老师包 time_count.c 已删 TIM6 每秒回写舵机，而 2609 新树 claw.c 注释仍依赖 TIM6 每秒回写 servo_angle2/3 —— 两棵树行为不同，改角度务必同步全局量（新树 claw.c 已做）。
- 该包 HEX 已删，须 Keil5 Rebuild `STM32_lower_car2\USER\Template.uvprojx` 再烧。

---

## 9. 接手会话（2026-09-05）改动清单

| 文件 | 改动 |
|---|---|
| 整机程序设计\deploy_pi\aim_and_place.py | pump_frames 喂帧统一先 rotate_ccw（与 aim_loop 同一坐标空间，修复 rotate 开启时 EMA/滑窗被混帧搅乱） |
| 整机程序设计\deploy_pi\calibrate_aim.py | grab_center 同样先 rotate_ccw（标定与瞄准同一坐标空间），删占位假代码 |
| 整机程序设计\deploy_pi\smoke_calib.py | 重写为 aim_and_place v4 API（原引用已不存在的 detect_circle → 必崩）；支持 --cam |
| 整机程序设计\deploy_pi\cam_diag_aim.py | 同上 v4 API 重写；支持 --cam/--frames；坐标与瞄准一致 |
| 整机程序设计\deploy_pi\README.md | 硬件检查节：26090501TEST 必须先 Rebuild；注明 09-05 修复内容 |
| 整机程序设计\deploy_pi\上位机指令对照表.md | smoke/cam_diag 参数与输出说明更新 |
| 整机程序设计\26090501TEST\CLEAN\TTLctrl.c / TTLctrl.h | 注释修正：0x16 夹料组为"手调占位参数,上车实测后定稿"（原注释称空骨架，误导） |
| .vscode\tasks.json | 同步任务 IP 192.168.3.30→10.169.170.145；新增 deploy_pi 一键部署/说明任务 |
| 整机程序设计\aim_and_place.py 等根级存档副本 | 与 deploy_pi 同步（md5 一致） |
| 整机程序设计\deploy_pi\aim_and_place.py（追加） | ACK 等待按命令类型区分：0x13/0x16 阻塞动作组 25s 等整组完成回执，杜绝下位机忙时后帧覆盖/丢命令（原 3s 必超时且会提前回放） |
| 整机程序设计\deploy_pi\test_aim_protocol.py（新增） | 协议层纯逻辑回归（帧/大端/ACK/常量/命令字，stub cv2·serial），**本机已跑 ALL TESTS PASSED**；另用 `py_compile` 通过 13 个改动/核心 .py 的语法门禁 |
| 整机程序设计\car_code上位机\task_code_v2.py + test_task_code_v2.py（新增） | 官方任务码解析地基：4组15字符/6色编号/位置/查重 + describe 翻译；**本机 9 项单测 ALL TESTS PASSED** |
| 整机程序设计\car_code上位机\cmd_pack.py + test_cmd_pack.py（新增） | 串口帧打包层：int8 限幅(-128..127,杜绝旧 128 钳位 bug)、16/32位大端、0x01~0x16 全部命令字；**本机 8 项单测过** —— 供真机 Executor 直接复用 |
| 整机程序设计\car_code上位机\serial_exec.py + test_serial_exec.py（新增） | 串口执行适配器：延迟 import pyserial、10B 帧校验、ACK 滑动窗、超时重发(retries);假串口 **7 项单测过** —— 与 cmd_pack 组成真机 Executor 的物理层 |
| 整机程序设计\car_code上位机\demo_fullflow_local.py（新增） | **纯逻辑端到端演示**: 任务码→编排19步→执行→统计→显示行;含失败/旧码边界;**本机跑通**。真机替换 FakeExecutor 即可 |
| 修正 task_stats.finished | 语义收紧: 全部抓到且放置达标才算"完成"(失败运行显示 Done N) |
| 整机程序设计\car_code上位机\task_stats.py + test_task_stats.py（新增） | 规则要求的统计账本(抓取/放置正确/环分/display_lines,同一物料多场合放置)；**本机 5 项单测 OK** |
| 整机程序设计\全流程上位机v2设计.md（新增） | 整场任务新上位机蓝图：分层架构、与旧高危项验收对照、落地顺序 |
| _pdf_extract\probe_palette.py（新增） | 图片主色调探针(PIL,支持中文路径) → 现场为 6 色识别取色定阈值 |

| 2026-09-05 晚：Pi 恢复在线并部署验证 | 生成本机 pi_deploy 密钥+ssh config+authorized_keys（免密）；deploy_pi 5 文件 md5 与本地一致；smoke 5/5 SMOKE_OK；run_all_tests 全绿；cam_diag_aim 20/20（当时视野圆心≈(1272,251) r≈234，std cx≈9.7/cy≈18.1 且贴右缘、digit 空 → 摆位/对焦待现场调）；aim_and_place --demo --stream 8090 HTTP 200 循环正常；无残留进程 |
| 修正 deploy_to_pi.ps1 | `$ErrorActionPreference=Continue`：原 Stop 被 ssh locale 警告(stderr)中断部署 |

⚠️ 验证范围说明：deploy_pi/瞄准套件与视觉回归已在 Pi 实机验证；全流程纯逻辑层为本机单测；**下位机 26090501TEST 仍缺 Keil Rebuild/烧录与车上动作组实弹**（0x13/0x16 手调参数待上车定稿）。

---

## 10. 下一步行动清单（接手后按优先级）

1. **（到场地第一步）下位机**：Keil5 打开 `26090501TEST` → Rebuild(0 Error) → 烧录 `OBJ\26090501TEST.hex`；在车上逐条实测 0x13/0x16 动作组并定稿参数（测试前架空/抬离执行机构）。
2. **树莓派部署**：`deploy_to_pi.ps1` 一键部署（IP 变则 `-Pi`）；`smoke_calib.py` 冒烟。
3. **现场标定**：机械/相机位置与上次不同 → `calibrate_aim.py` 重标 K 填入 aim_and_place.py 顶部；按 README §四 实测 CAM_GRIP_OFFSET_X/Y。
4. **现场微调视觉参数**：ring demo 的 digit_thr/center_gate_r；现场数字若是印刷字体则补模板库。
5. **瞄准放料实弹**：--stream 看画面 → 分步 home/grab/replay → mission 全流程。
6. **高光鲁棒性（暂停项，可现场捡起）**：用实拍环图复跑 `_pdf_extract\exp_highlight_ring.py`（暗通道+多轮廓合并圆拟合 vs 原版最大连通域；脚本已改为自动找 `工创赛标靶A4(12/13).png`，也可 `python3 exp_highlight_ring.py 图.png` 直接传参，耗时对比 `bench_pipeline.py`）；若要并入 ring_center_detect_v2，必须同时过三套回归+真实图。
7. **全流程搬运（大工程，若要做整场）**：旧 car_code上位机 高危问题依然适用（官方 15 字符任务码 4 组解析、6 色编号、颜色→位置映射、全链路超时），见 `上位机程序分析.md` 与下节红线提醒。
   ★2026-09-05 已落地语义层地基（纯 Python、本地 9 项单测通过）：`整机程序设计\car_code上位机\task_code_v2.py`（官方任务码解析:4组/6色编号/位置/查重,含 describe 人话翻译与命令行用法）+ `test_task_code_v2.py`（`python test_task_code_v2.py` → ALL TESTS PASSED）。新版全流程上位机直接 `from task_code_v2 import parse_task_code` 替换旧 get_taskinfo；颜色映射旧代码 [0,red,green,blue] 已被官方表取代。
   ★同一批还交付：`car_code上位机\task_stats.py`+`test_task_stats.py`（规则要求的抓取/放置统计+环分+display_lines,支持同一物料多场合放置,5 项单测过）、`flow_runner.py`+`test_flow_runner.py`（编排骨架:任务链/依赖跳过/重试/总时长与静止看门狗/统计联动,7 项单测过）、`cmd_pack.py`+`test_cmd_pack.py`（0x01~0x16 帧打包,int8/16/32 位大端,8 项单测过）、`serial_exec.py`+`test_serial_exec.py`（串口执行:延迟 pyserial/ACK/重发,7 项单测过）与蓝图 `整机程序设计\全流程上位机v2设计.md`。纯逻辑层(解析→统计→编排→打包→串口执行)已齐,下一步=把 vision(读码/色/环)接进 Executor 并车上联调。
   ★2026-09-12 适配(你改的下位机动作组)：`TTLctrl.c` 拆成 `TTLctrl_Act_GrabFromVehicle_1/_2/_3()`(物架 1/2/3 号位, 1=base正转1700、2=base不动、3=base反转1700,末尾均回观察位),新增统一入口 `TTLctrl_Act_GrabFromVehicle(uint8_t slot)`;`TTLctrl.h` 声明、`protocol_and_main.c` 的 0x16 取 `buf[1]` 位号、`car_config.h` 注释同步。上位机：`aim_and_place.py` 的 `Lower.grab_from_vehicle(slot)` 发 `0x16 <slot>`,新增 `--slot {1,2,3}` 与 **`--mission-all`**(瞄准一次→依次取 1/2/3 号位各放一次);`cmd_pack.pack_grab_vehicle(slot)` + 回归用例(aim 协议新增 6 项、cmd_pack 新增位号断言)全绿；对照表/README 已同步。**固件需 Rebuild 后才能用。**
   ★2026-09-12 新增视觉调参台(浏览器版, Pi 零窗口)：`ring_stream_ui.py`(圆环识别中心:全 CALIB 滑条 + 诊断面板[原始vsEMA圆心/φ1/数字各类分数/投票/耗时] + 自动估参 + 一键加模板 + 一键回归)与 `digit_templates_ext.py`(打印体现场样例热加模板, 目录 `digit_custom/<1|2|3>/*.png`)。实测: 模板构建 6.7s(后台不阻塞 UI)、A4(12) → digit=2 score=1.0、加模板后该类分数 0.469→0.999、自动估参出 close_k=23、合成 1280×720 fps≈5.8(loop 126ms)。配合 `color_blob_stream.py`(颜色)构成"零窗口+浏览器"的视觉调参路线, 避免 Qt 桌面版把树莓派卡死。

> 红线提醒（与旧分析一致）：全链路必须加超时（"停止15秒"即本轮结束）；RGB2HSV 通道互换是特性非 bug，勿单独改；pkill 自匹配/PowerShell 引号/摄像头独占，见 lecture.md 十大坑。

---

## 11. DSH Web GUI 插件安装备忘（2026-xx 实操记录，供后续复用）

DSH_HOME=`C:\Users\Justin\.dsh`，web profile=`profiles\web`（bundle 层 = package.json 的 `dsh.profile.bundles` + `patchReload: live`）。
已安装（需重启 `启动DeepSeekHarnessWeb.cmd` 后生效，浏览器 Ctrl+F5）：
- npm registry：`dsh-status-rotator@0.11`（状态轮播/弹幕）、`dsh-theme-plugin@0.3`（主题工作室）、`dsh-think-zh-expand@0.4`（思考中文+默认展开，含其依赖 `dsh-md-render`）、`dsh-trail-bundle@0.1`+`dsh-trail@0.1`（轨迹页新视图，自动禁用官方 ui-trajectory）；
- `dsh-web-launcher@0.2.4`：npm 包名已被 hanwuji1 的 0.3.x 占用且 0.3.2 漏发 cordis.patch.yml（bundle 元数据坏）→ 改从用户指定源 `ayingQAQ/dsh-web-launcher` 用 `npm pack` 打包后本地 `add <abs>.tgz` 安装；tgz 归档在 `profiles\web\vendor\dsh-web-launcher-0.2.4.tgz`（package.json 的 `file:` 依赖指向它，勿删）。
- 第二批：`dsh-plugin-wallpaper-engine@0.7`（Wallpaper Engine 壁纸背景，Host 读 Steam 库 + 同源 HTTP 路由，较重：含 jpeg-js/glsl-parser 依赖与 peer @deepseek-ai/dsh-client-runtime、dsh-client-ui-slots）、`dsh-whale-widget@0.2.10`（右下角余额小鲸鱼）；均 npm 直装成功，dump-config 已含 `wallpaper-engine`/`dsh-whale-widget` 行。
- 第三批：`dshmarket@1.45.1`（= dsh-market/dsh-market，DSH 可视化插件市场，官网 dshmarket.com；bundle 行 id 为 `dsh-market`，client 注入 locale/ui-settings/ui-theme，deps js-yaml+undici，peer 要求 @deepseek-ai/dsh-settings rc.7+ 已满足）——装上后可在 GUI 里浏览/一键安装社区插件。
经验：
1. 装插件：`npm i -g pnpm` 后 `dsh plugin --profile web add <包名>`（= profile 目录内跑 pnpm add，装完自动把带 `dsh.bundle.patch` 的依赖并入 bundles 层）。插件不在 git 环境可用性内时 github: 依赖不可用（pnpm 需 git ls-remote），用 `npm pack` + `add <abs>.tgz` 兜底。
2. 坑：BOM——`readProfileManifest` 直接 `JSON.parse`，包 package.json 带 UTF-8 BOM 会崩（dsh-web-launcher 0.3.2 实测中招），删前 3 字节即可。
3. 校验不重启：`dsh --profile web --dump-config` 单进程组合配置树（exit 0 + 各插件行出现 = 就绪）；bundle 装完必须重启 GUI 才生效（patchReload live 只热载 cordis.patch.yml，不含 package.json 的 bundles 变更）。
4. 运行中的 GUI 进程即当前会话宿主，勿自行 kill；重启 = 关启动窗口再跑 .cmd（dsh-web-launcher 激活后会生成桌面「DSH Web」快捷方式，此后可一键后台重启+刷新浏览器）。
