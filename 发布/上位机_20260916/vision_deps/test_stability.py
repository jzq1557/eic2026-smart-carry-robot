# -*- coding: utf-8 -*-
"""数字稳定性(滑动窗口众数)测试: 场景1-5。
窗口 = 最近 N 次(默认20)高置信读数, 输出 = 其中最多的数字; 不锁定, 随读数滚动。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import numpy as np
import ring_center_detect_v2 as m
from test_rotation import make_target


def blank_frame():
    return np.full((520, 520, 3), 255, np.uint8)


def run(det, frames):
    return [det.process(f) for f in frames]


N = m.CALIB['digit_votes']          # 20

# 场景1: 窗口 14x'2' + 6x'1' -> 输出众数 '2', 占比 0.7
det = m.RingDetector(m.CALIB)
res = run(det, [make_target('2', 0)] * 14 + [make_target('1', 0)] * 6)[-1]
assert res['digit'] == '2' and abs(res['digit_score'] - 0.7) < 1e-6, res
print('S1 mode-on-2 OK:', res['digit'], res['digit_score'])

# 场景2: 不锁定, 窗口滚动 —— 再喂 8 次 '1', 窗口变为 6x'2'+14x'1' -> 输出翻转为 '1'
for _ in range(8):
    res = det.process(make_target('1', 0))
assert res['digit'] == '1' and abs(res['digit_score'] - 0.7) < 1e-6, res
print('S2 window-rolls-to-1 OK:', res['digit'], res['digit_score'])

# 场景3: 环短暂丢失(未超阈值) -> 窗口保留, 输出仍是 '1'
r = det.process(blank_frame())
assert (not r['found']) and r['digit'] == '1', r
print('S3 short-loss keep OK')

# 场景4: 连续丢失超过阈值 -> 清空窗口(输出 None); 新环 '3' 重新累积 -> 输出 '3'
for _ in range(m.CALIB['digit_unlock_stale'] + 2):
    det.process(blank_frame())
r = det.process(make_target('3', 0))
assert r['digit'] == '3' and len(det.votes) == 1, r
res = run(det, [make_target('3', 0)] * (N - 1))[-1]
assert res['digit'] == '3' and len(det.votes) == N, res
print('S4 re-accumulate-on-3 OK:', res['digit'], res['digit_score'])

# 场景5: 环心跳变(>jump_px) -> 清空窗口, 对新环 '2' 重新累积
det2 = m.RingDetector(m.CALIB)
run(det2, [make_target('3', 0)] * (N + 1))
off = np.full((820, 820, 3), 255, np.uint8)           # 环心移到 (540,540):
t = make_target('2', 0)                               #  距画面中心(410,410)=184px(门限内), 跳280px
off[280:800, 280:800] = t[:520, :520]
res = det2.process(off)
assert res['found'] and len(det2.votes) == 1, '跳变后窗口应被清空并重新累积'
res = run(det2, [off] * (N + 1))[-1]
assert res['digit'] == '2' and len(det2.votes) == N, res
print('S5 jump-clear-re-accumulate-on-2 OK:', res['digit'], res['digit_score'])

# 场景6: 环心超出画面中心半径200px圆 -> 不识别
det3 = m.RingDetector(m.CALIB)
big = np.full((820, 820, 3), 255, np.uint8)
t = make_target('2', 0)
big[0:520, 0:520] = t                                 # 环心(260,260)距画面中心(410,410)=212px>200
res = det3.process(big)
assert not res['found'], res
print('S6 center-gate reject OK')

print('ALL WINDOW-MODE TESTS PASSED')
