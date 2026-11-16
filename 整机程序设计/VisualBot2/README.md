# VisualBot2 —— **只跑图**（底盘路线 + 视觉看画面）

> 目的：把「车走到位 + 相机看得见」这条主线**单独**调明白。
> 机械臂/龙门架/夹爪/取料放置动作组**完全不参与** —— 于是"车走歪了"只可能是
> **里程参数**或**底盘本身**的问题，不会再被瞄准没收敛、机械臂撞了这类毛病互相掩盖。
>
> 与老工程的关系：**并存**。`/root/VisualBot`（完整工程，含机械臂那套）原封不动，
> 随时可对照；路径调稳后再把作业动作接过来。

---

## 一、里面有什么

| 文件 | 干什么 |
|---|---|
| `route_main.py` | **路线主程序**：7 段走位（含 base 作业姿态、扫码），每段跑完**停下等你回车**；跑起来时自动开双摄推流 |
| `route_car.py` | **底盘动作库**：`fwd/back/left/right/spin/goto_cm` + `base_left/base_right`。**故意不含**伸缩台/龙门架/夹爪 |
| `route_debug.py` | **走位标定助手**：里程 / 自旋 / base 90° 三个标定，都是"发一段 → 你量实际值 → 它算该填多少" |
| `monitor_ui.py` | 双摄推流页面（复用；跑图时由 `route_main` 自动拉起） |
| `ring_stream_ui.py` / `color_blob_stream.py` | 圆环 / 颜色调参台（复用，浏览器零窗口） |
| `vision.py` · `cam_rotate.py` · `lower_api.py` · `ring_center_detect_v2.py` · `color_blob_demo.py` | 视觉门面 / 画面方向 / 串口协议 / 两个识别库（复用） |
| `check_env.py` | 环境自检：我在哪个目录跑、加载的哪份代码、档案从哪读、相机串口在不在 |
| `exposure_tune.py` | 按当前场景挑曝光档（过曝/太暗一眼看出） |
| `deploy_visualbot2.ps1`（Windows 侧） | 一键部署本工程到 `/root/VisualBot2` |

---

## 二、怎么用

### 1) 跑图（分段，推荐）

```bash
cd /root/VisualBot2
python3 -u route_main.py --port /dev/stm32
```
- 每跑完一段会停住，打印 **【要量的东西】**，按回车走下一段（输入 `q` 退出）
- 浏览器同步看画面：`http://<板子IP>:8092/`（左=前方作业相机，右=侧装扫码相机，**扫码结果直接显示在右路**）

| 开关 | 作用 |
|---|---|
| `--auto` | 一口气跑完，不在每段后停 |
| `--start N` | 从第 N 段开始（1~7） |
| `--no-monitor` | 不开推流 |
| `--dry-run` | 不连车、不动电机（先验证流程） |

### 2) 标定（三步，做完里程就准了）

```bash
python3 -u route_debug.py --port /dev/stm32
```
| 键 | 标什么 | 怎么量 |
|---|---|---|
| `m` | **里程**（选一段发车） | 用尺量车实际走了多少 cm |
| `s` | **自旋**（发 SPIN_DEG） | 车身贴参照物，量实际转了多少度 |
| `b` | **base 90°**（发 BASE_90 脉冲） | 量车头方向与机械臂指向的夹角 |
| `g` | 手动发一段（方向+距离 / 自旋+角度） | 随手试 |
| `p` | 看当前参数 | — |
| `h` | 急停 | — |

原理都是**比例法**：`需要的值 = 当前值 × 目标 ÷ 实测`。
例：发 12cm 实测 11cm → 建议 `RIGHT_OUT = 13.1`（12×12/11）。

---

## 三、参数在哪调（`route_main.py` 顶部参数区，改完直接重跑）

```python
CHASSIS_VEL / ACC / DEC     底盘档位
BASE_90 / TTL_VEL           base 转 90° 的脉冲与速度
RIGHT_OUT                   ① 右移出启动区
TO_SCAN                     ② 前进到扫码位
TO_MATERIAL                 ③ 进原料区
TO_PROCESS                  ④ 出原料区后前进
SPIN_DEG                    ⑤ 原地自旋角度
ENTER_PROCESS               ⑥ 自旋后进加工区
TO_FINISH / FINISH_RIGHT    ⑦ 出加工区前进 / 右移进结束位
CAM_INDEX / CAM_ROTATE / CAM_EXPOSURE / CAM_WIDTH   相机
MONITOR / MONITOR_PORT / MONITOR_SCAN_CAM           推流
```

⚠️ 两个注意：
- **方向符号以固件为准**（左右：`cm<0=右移`；自旋：`deg<0=顺时针`），已在 `level_api`/`route_car` 里处理，
  换底盘代码前先读一遍 README 坑档案 #47。
- **扫码相机被推流占着**：`route_main` 不自己开相机，而是**问推流页面的 `/state` 要解码结果**
  （连续 3 帧一致才采纳）—— 同一台相机同一时刻只能被一个进程打开，这样才不打架。
  所以扫码段要保证 **推流是开着的**（别加 `--no-monitor`）。

---

## 四、排错

| 现象 | 怎么办 |
|---|---|
| 串口打不开 | `ls /dev/ttyUSB*`；建议统一用 **`/dev/stm32`**（已建 udev 规则固定，插拔不变号） |
| 相机打不开 | 有别的进程占着：`pgrep -af python3` → `pkill -f <名字>`；`check_env.py` 能一眼看出 |
| 画面过曝/太暗 | `python3 -u exposure_tune.py --color red` 按当前场景挑档 |
| 画面是竖的 / 目标变椭圆 | 画面方向没选对：监控台顶部四个按钮点一次（会存进 `ring_calib.json`） |
| 车走歪了 | 大概率是里程参数 → 用 `route_debug.py` 的 `m` 重标；若"指令 12 实测 8"这种差得太多（<0.9 或 >1.1），
改用 `calibrate_route.py` 查底盘 `STEPS_PER_CM`，那属于固件层面 |
| 不知道自己在跑哪份代码 | `python3 -u check_env.py` |

---

## 五、跑通之后的下一步

路线稳了（每段实测/指令比例都在 0.9~1.1、base 正好 90°、扫码能稳定读码），再回到
`/root/VisualBot` 把作业动作接上：取料动作组（瞄颜色 → 下龙门 → 夹 → 抬）、
放置动作组（瞄圆环 → 回位取物架料 → 回放 → 下龙门 → 开爪 → 抬）。
那时"车走位"这一层已经不用再怀疑了。
