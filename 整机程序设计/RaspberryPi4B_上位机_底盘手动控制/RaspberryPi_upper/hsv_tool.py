"""
HSV 标定工具(自制加强版)
- 自绘控制面板:6 根横向滑条竖排,每根带清晰文字标签和当前数值,窗口一打开全部可见
- 左键点击/拖动滑条改值
- 键盘微调:选中行(点击行或按 1~6),左右方向键 -1/+1
- 按 s:打印当前阈值(low/high 数组,直接可贴进 config.py)
- 按 r:重置全开
- 按 Esc / q:退出
用法: python3 hsv_tool.py            (默认摄像头0)
      python3 hsv_tool.py 1          (摄像头1)
"""
import cv2 as cv
import numpy as np
import sys

CAM = int(sys.argv[1]) if len(sys.argv) > 1 else 0

NAMES  = ["H low", "H high", "S low", "S high", "V low", "V high"]
MAXVAL = [180, 180, 255, 255, 255, 255]
vals   = [0, 180, 0, 255, 0, 255]          # 初始全开

PANEL_W, ROW_H, TOP = 420, 64, 20
BAR_X0, BAR_X1 = 130, PANEL_W - 70
selected = 0

def draw_panel():
    p = np.zeros((TOP + ROW_H * 6 + 20, PANEL_W, 3), dtype=np.uint8)
    p[:] = (35, 35, 35)
    for i in range(6):
        y = TOP + i * ROW_H + ROW_H // 2
        hl = (0, 255, 255) if i == selected else (200, 200, 200)
        cv.putText(p, NAMES[i], (12, y + 6), cv.FONT_HERSHEY_SIMPLEX, 0.62, hl, 2)
        cv.line(p, (BAR_X0, y), (BAR_X1, y), (90, 90, 90), 4)
        kx = int(BAR_X0 + (BAR_X1 - BAR_X0) * vals[i] / MAXVAL[i])
        cv.circle(p, (kx, y), 11, (0, 200, 255) if i == selected else (160, 160, 160), -1)
        cv.putText(p, str(vals[i]), (BAR_X1 + 12, y + 7),
                   cv.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    cv.putText(p, "click/drag bar | arrows fine-tune | s=print r=reset Esc=quit",
               (10, TOP + ROW_H * 6 + 12), cv.FONT_HERSHEY_SIMPLEX, 0.42, (140, 220, 140), 1)
    return p

def on_mouse(event, x, y, flags, _):
    global selected
    if event == cv.EVENT_LBUTTONDOWN or (event == cv.EVENT_MOUSEMOVE and flags & cv.EVENT_FLAG_LBUTTON):
        row = (y - TOP) // ROW_H
        if 0 <= row < 6:
            selected = int(row)
            ratio = (x - BAR_X0) / (BAR_X1 - BAR_X0)
            vals[selected] = int(round(min(max(ratio, 0), 1) * MAXVAL[selected]))

cap = cv.VideoCapture(CAM)
assert cap.isOpened(), "camera not opened"
cv.namedWindow("panel")
cv.setMouseCallback("panel", on_mouse)
print("HSV tool started. Windows: panel(sliders) / res(filtered view)")

while True:
    ok, img = cap.read()
    if not ok:
        continue
    hsv = cv.cvtColor(img, cv.COLOR_BGR2HSV)
    low  = np.array([vals[0], vals[2], vals[4]])
    high = np.array([vals[1], vals[3], vals[5]])
    mask = cv.inRange(hsv, low, high)
    res  = cv.bitwise_and(img, img, mask=mask)

    cv.imshow("panel", draw_panel())
    cv.imshow("res", res)

    k = cv.waitKey(1) & 0xFF
    if k in (27, ord('q')):
        break
    elif k == ord('s'):
        print("low_thresh  = [{}, {}, {}]".format(vals[0], vals[2], vals[4]))
        print("high_thresh = [{}, {}, {}]".format(vals[1], vals[3], vals[5]))
    elif k == ord('r'):
        vals[:] = [0, 180, 0, 255, 0, 255]
    elif k in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6')):
        selected = k - ord('1')
    elif k == 81:            # left arrow
        vals[selected] = max(0, vals[selected] - 1)
    elif k == 83:            # right arrow
        vals[selected] = min(MAXVAL[selected], vals[selected] + 1)

cap.release()
cv.destroyAllWindows()
