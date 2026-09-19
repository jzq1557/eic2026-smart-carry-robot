# lecture.md —— 通过 SSH 开发树莓派视觉上位机 · 全流程教学（历史附录）

> ⚠️ **已被根目录 `README.md` 整合取代**（2026-09-12）：坑表、开发循环、命令速查都在新 README 里（且已更新到 09-12）。
> 本文保留为**教学展开版**：SSH 五步循环、测试体系、命令速查的详细讲解，入门时仍值得一读。

> 读者：下一个接手的 agent（或想快速上手本项目的人）
> 目标：看完本文 → 能独立完成"改代码 → 上机验证 → 交付"的完整循环，不踩前人踩过的坑
> 配套：`continue.md`（工程状态交接）、`_pdf_extract/`（本地工作副本）、树莓派 `/home/justin/vision_robot/`
> 最后更新：2026-09-01（基于两轮实战会话）

---

## 0. 五句话速览（先读这个）

1. **开发循环**：本地 `_pdf_extract/` 改 → `scp` 到树莓派 → Pi 上跑测试 → 验证 → 更新存档与文档。
2. **树莓派**：`ssh 10.169.170.145`（用户 justin，密钥已在 `~/.ssh/config` 配好），代码在 `/home/justin/vision_robot/`。
3. **最大敌人是 PowerShell 引号转义**：远程执行含引号/嵌套命令的 python 必坏 —— **一律写脚本文件 scp 过去再跑**，不要内联。
4. **摄像头一次只能被一个进程占用**：残留 demo 进程会让新程序报 `can't open camera by index`，先 `ps aux | grep python3` 清理。
5. **本地无可用 Python**：一切实测只能在 Pi 上做；离线图片也能测（demo 支持 `python3 xxx.py 图片.jpg`，已修复为跑完自动退出）。

---

## 1. 环境与连接

### 1.1 连接树莓派

```bash
# 本机（Windows PowerShell 或 git bash）
ssh 10.169.170.145
# 应看到 hostname = RaspberryPi；用户 justin，密钥 ~/.ssh/pi_deploy 自动匹配
```

- `~/.ssh/config` 已写好（Host/User/IdentityFile），无需每次指定参数。
- **旧地址 192.168.3.30 已不可达**，别用；换网络时改 config 里的 HostName。
- ssh 每次会打印 `bash: warning: setlocale: LC_ALL: cannot change locale (zh_CN.GB18030)` —— **无害**，忽略（它还会让 PowerShell 把 stderr 当错误显示 exit code 1，但命令实际成功了，看 stdout 判断）。

### 1.2 目录结构

```
本机 E:\工程文档\27工创赛\
├── continue.md              # 工程状态交接（每次会话后更新）
├── lecture.md               # 本文
├── _pdf_extract\            # ★ 本地工作副本（唯一编辑区，改检测/推流 demo）
│   ├── ring_center_detect_v2.py   # 圆环+数字识别 demo
│   ├── qr_stream_demo.py          # 二维码识别 demo
│   ├── test_rotation.py / test_dashed.py / test_stability.py  # 回归套件
│   ├── run_all_tests.py           # 一键全验证
│   ├── verify_rotate.py           # 旋转坐标验证
│   ├── focus_assist.py            # 物理调焦辅助
│   ├── diag_real_images.py        # 真实图失败诊断
│   └── tasks_pi.json              # Pi 的 VSCode tasks
├── 整机程序设计\            # 存档副本 + 下位机/老师车工程
│   ├── deploy_pi\           # ★ 当前瞄准/放料主线(aim_and_place 等 5 脚本 + README + 指令对照表)
│   ├── 26090501TEST\        # ★ 当前下位机最新固件(CLEAN + TTL 闭环; 需 Rebuild 再烧)
│   └── …（2609/2607/老师车/F407 等历史工程）
└── 工创赛标靶A4(12/13).png  # 真实标靶图（离线验证用）

树莓派 /home/justin/vision_robot\
├── ring_center_detect_v2.py / qr_stream_demo.py   # 实际运行文件（与本地 md5 一致）
├── test_*.py / run_all_tests.py / verify_rotate.py / focus_assist.py
├── .vscode\tasks.json      # 6 个一键任务
└── 工创赛标靶A4(12/13).png  # 测试图（scp 过去）
```

---

## 2. 标准开发循环（最重要的一章）

任何改动都走这个五步循环，**顺序不要乱**：

### 第 1 步：本地编辑
只改 `E:\工程文档\27工创赛\_pdf_extract\` 下的文件（这是"唯一事实源"，存档副本与 Pi 都从它同步）。

### 第 2 步：同步到 Pi

```powershell
scp "E:\工程文档\27工创赛\_pdf_extract\ring_center_detect_v2.py" `
    justin@10.169.170.145:/home/justin/vision_robot/
```

- 可一次传多个文件；新增脚本也要传（`run_all_tests.py` 等）。
- scp 中文文件名没问题（如 `工创赛标靶A4(12).png`）。

### 第 3 步：Pi 上跑测试
```bash
cd /home/justin/vision_robot
python3 test_rotation.py    # 期望 36/36 correct
python3 test_dashed.py      # 期望 PASS
python3 test_stability.py   # 期望 ALL WINDOW-MODE TESTS PASSED
```
**偷懒法**：`python3 -u run_all_tests.py` —— 一条命令跑完语法检查+三套回归+真实标靶图+二维码。

### 第 4 步：实测验证（视改动而定）
| 改了什么 | 怎么验证 |
|---|---|
| 检测/识别逻辑 | 三套回归 + `run_all_tests.py` |
| 坐标/旋转 | `verify_rotate.py`（断言坐标映射公式） |
| 摄像头/推流 | 后台起 demo + curl 检查 + 看日志（见 §4.2） |
| 图片模式 | `python3 demo.py 图片.jpg`（跑完自动打印 `[result]` 退出） |

### 第 5 步：收尾（别忘了）
1. 更新存档：`Copy-Item _pdf_extract\xxx.py 整机程序设计\xxx.py -Force`
2. 更新文档：`continue.md`（状态）、`lecture.md`（经验）——**这是给下一个 agent 的交接，不写等于没做**
3. 确认 Pi 无残留进程（§4.3），本地与 Pi md5 一致可顺带核对

---

## 3. 测试体系详解

### 3.1 回归套件（合成靶标）
- `test_rotation.py`：按 CALIB 精确画 6 环合成靶标，数字 1/2/3 × 12 角度 → 36/36。
- `test_dashed.py`：虚线环场景（φ2φ5 虚线 / φ1 虚线）→ φ1 估计正确。
- `test_stability.py`：滑动窗口众数逻辑（滚动/翻转/丢失保持/超时清窗/跳变清窗/位置门拒绝）6 场景。
- 这些测试**直接调 `RingDetector.process()`**，不走 main —— 所以改 main（如旋转、推流）不会影响它们，反之亦然。

### 3.2 真实图片验证（合成靶标之外的补盲）
`run_all_tests.py` 里会用 `工创赛标靶A4(12/13).png` 和 `test_qr.png` 做真实图识别：
```
[result] found=True center=(2597,1321) r=638 digit=2(1.00)   # A4(12)
[result] QR=156+123+516+231                                   # test_qr.png
```
**教训**：合成靶标全过 ≠ 真实图能过。上届就栽在"真实 A4 标靶图 found=False"，原因是 `center_gate_r` 位置门把环心不在画面中心 200px 内的图全拒了（图片模式已修复为自动关 gate）。

### 3.3 旋转坐标验证
`verify_rotate.py`：同一张图原图 vs 手工 `cv.rotate` 后各自检测，断言旋转后坐标 = `(y, W-1-x)`（逆时针90°），QR 角点误差 < 2px。

---

## 4. 摄像头操作（高频踩坑区）

### 4.1 一次只能一个进程用摄像头
**现象**：`cv2.VideoCapture(0)` 报 `can't open camera by index`，但摄像头明明插着。
**原因**：有残留 demo 进程占着 `/dev/video0`（SSH 断开不会自动杀后台进程！`timeout` 到期也会留下 `bash -c` 包装进程）。
**排查**：
```bash
ps -eo pid,args | grep python3 | grep -v grep
# 例: 4375 python3 ring_center_detect_v2.py --stream   ← 就是它
kill 4375
```
**预防**：测试用 `timeout N python3 xxx.py ...` 包一层；收尾时再查一次。

### 4.2 推流验证套路（无显示器）
```bash
cd /home/justin/vision_robot
# 后台起 demo（timeout 自动停），然后 curl 验证 HTTP
(timeout 12 python3 -u qr_stream_demo.py --stream 8082 >/tmp/qr.log 2>&1 &)
sleep 6
curl -s -m 3 -o /dev/null -w 'http %{http_code} size=%{size_download}\n' http://127.0.0.1:8082/
sleep 8
tail -3 /tmp/qr.log    # 看 [perf] fps 与识别输出
```
- HTTP 200 + 有 fps 输出 = 正常。画面无靶标/码时 `not found` 是正常的。
- `http://10.169.170.145:8080/` 可从自己电脑浏览器打开看真实画面（MJPEG）。

### 4.3 本摄像头对焦结论（重要）
**Microdia LRCP G720P（0c45:1915）是定焦摄像头，无对焦马达，软件调焦无效**：
- `v4l2-ctl -d /dev/video0 --list-ctrls`：无 focus 控件；
- OpenCV `CAP_PROP_FOCUS/AUTOFOCUS` 返回 -1.0；
- UVC 描述符（`lsusb -v -d 0c45:1915`）Camera Sensor 终端无 Focus bit。
- 要焦点在 ~20cm 地面 → **物理拧镜头**，用 `focus_assist.py --stream 8090` 看清晰度分数（拉普拉斯方差，越大越清晰）辅助调。

---

## 5. 十大实战坑（血泪清单，每条都真实踩过）

1. **PowerShell 引号地狱（最坑，没有之一）**
   ```powershell
   # ✗ 这种必坏——引号/转义/嵌套全乱
   ssh pi "python3 -c \"import ast; ...\""
   # ✓ 正确做法：本地写脚本 → scp → ssh 里跑
   scp my_script.py justin@10.169.170.145:/home/justin/vision_robot/
   ssh 10.169.170.145 "cd ~/vision_robot && python3 -u my_script.py"
   ```
   内联 python 的双引号、`$`、反斜杠、中文全会被 PowerShell 和 ssh 双层转义搞坏。**写文件最稳**。

2. **残留进程占摄像头** → 见 §4.1。SSH 断线不杀后台进程。

3. **pkill/pgrep 自匹配**：`pkill -f 'ring_center'` 会杀掉命令行里含该串的**自己**（bash -c 包装进程）。用 PID 杀，或 `ps ... | grep -v grep` 先看清楚。

4. **图片模式在 SSH 下无限循环**（已修，但要知道）：早期 `python3 demo.py 图.jpg` 在无显示环境降级 no-show 后 `while True` 永不退出 → 表现为命令超时。现在处理完打印 `[result]` 自动退出。**看到命令超时卡死，先怀疑这类死循环**。

5. **ssh 的 locale 警告**（`setlocale: cannot change locale`）：无害。但它走 stderr，PowerShell 会把命令标成 exit code 1 —— 看 stdout 与输出内容判断成败，别被红色吓到。

6. **`self.cal = dict(calib)` 是拷贝**：改 `cfg` 字典要在 `RingDetector(cfg)` 创建**之前**，之后改无效（真实踩过：图片模式关 gate 加错位置，先加了等于没加）。

7. **回归测试绕过 main**：测试直接调 `process()`，不会经过 main 里的初始化逻辑（旋转、gate 关闭、detect_every 修正都不生效）——所以 main 层的修复要**自己写验证**（如 verify_rotate.py、run_all_tests.py 里的真实图）。

8. **`detect_every` 节流会坑图片模式**：qr demo 隔 N 帧才解码，图片模式只跑 1 帧时首帧可能不解码 → 必然"未识别"。已修（图片模式强制 detect_every=1）。**改任何"隔帧/节流"逻辑时想想单帧/图片场景**。

9. **本地无 Python**：`_pdf_extract` 里只有脚本没有环境，别在本机跑 python 验证（有 node 可跑 test_qr_tool.js 那种纯 JS）。

10. **改完必须四同步**：本地副本 → Pi → 存档（`整机程序设计\`）→ 文档（`continue.md`）。只改一处 = 白改。

---

## 6. 诊断套路（遇到问题按此排查）

### 6.1 "相机打不开"
1. `ps -eo pid,args | grep python3 | grep -v grep` → 杀残留。
2. `ls -la /dev/video*` → 设备节点存在吗？
3. `v4l2-ctl -d /dev/video0 --all` → 驱动层面能看到吗？
4. 还不行再看 `dmesg | tail`。

### 6.2 "真实图识别失败"（合成靶标过但真图不过）
跑 `diag_real_images.py`，看输出里的：
- `gray mean/std`：图是不是太暗/太亮/对比低；
- 二值化后 `white%`：环线提取比例是否合理；
- `top5 contour areas` vs `min_area_frac threshold`：最大连通域是不是被面积阈值滤掉了；
- 直接调 `find_center` 的结果：检测本身过不过，还是被后面的 gate/窗口逻辑拒的（`center_gate_r` 是最常见凶手，离线图直接设 0）。

### 6.3 坐标/方向不对
- 画面旋转需求 → 读帧后立即 `cv.rotate(ROTATE_90_COUNTERCLOCKWISE)`，坐标自动跟随（映射 `(x,y)→(y,W-1-x)`，半径不变）。用 `verify_rotate.py` 断言。
- 记得 Pi 上画面是旋转后的，浏览器看到的也是旋转后的，别拿原图坐标去对。

### 6.4 对焦/画质问题
1. `v4l2-ctl --list-ctrls` 看有没有 focus → 没有就死心，物理拧镜头（§4.3）。
2. 清晰度用拉普拉斯方差量化（`focus_assist.py`），不用人眼猜。

---

## 7. 关键命令速查

```bash
# ---------- 连接 ----------
ssh 10.169.170.145                     # 登入（密钥自动）

# ---------- 同步（本机 PowerShell） ----------
scp "E:\工程文档\27工创赛\_pdf_extract\xxx.py" justin@10.169.170.145:/home/justin/vision_robot/

# ---------- 测试（Pi 上） ----------
cd /home/justin/vision_robot
python3 -u run_all_tests.py            # 一键全验证（推荐）
python3 test_rotation.py && python3 test_dashed.py && python3 test_stability.py
python3 verify_rotate.py               # 旋转坐标断言

# ---------- 运行 demo ----------
python3 ring_center_detect_v2.py --stream          # 浏览器 http://10.169.170.145:8080/
python3 qr_stream_demo.py --stream
python3 ring_center_detect_v2.py 图片.jpg           # 离线单图（跑完自动退出打印 [result]）

# ---------- 推流冒烟（timeout 防残留） ----------
(timeout 12 python3 -u qr_stream_demo.py --stream 8082 >/tmp/x.log 2>&1 &)
sleep 6; curl -s -m 3 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8082/

# ---------- 清理 ----------
ps -eo pid,args | grep python3 | grep -v grep     # 找残留
kill <PID>                                         # 用 PID 杀，别 pkill -f

# ---------- 摄像头诊断 ----------
v4l2-ctl -d /dev/video0 --list-ctrls               # 控件列表（有没有 focus 一目了然）
lsusb -v -d 0c45:1915 | grep -A8 'Camera Sensor'   # UVC 终端能力
python3 focus_assist.py --stream 8090              # 物理调焦辅助（清晰度分数）
```

---

## 8. 接手自检清单（新 agent 第一天上手）

- [ ] `ssh 10.169.170.145` 通，`hostname` = RaspberryPi
- [ ] `cd ~/vision_robot && python3 -u run_all_tests.py` 全绿（含真实图 [result]）
- [ ] 本地 `_pdf_extract` 与 Pi 关键文件 md5 一致（`md5sum` vs `Get-FileHash -Algorithm MD5`）
- [ ] 浏览器能开 `http://10.169.170.145:8080/` 看到推流
- [ ] 读一遍 `continue.md` §2.4/§2.5 的"本轮改动"，知道最近改了什么、为什么
- [ ] 知道 5 步循环：本地改 → scp → 测试 → 验证 → 收尾（存档+文档）
- [ ] 记住最痛的一条：**远程跑 python 一律写脚本文件 scp 过去，不内联**
