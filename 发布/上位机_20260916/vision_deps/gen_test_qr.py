# -*- coding: utf-8 -*-
"""生成测试二维码PNG(任务码内容)并用 qr_stream_demo 的解码函数验证。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import qr_stream_demo as m

code = '156+123+516+231'
# 用 OpenCV QRCodeEncoder 生成(带白边)
params = cv2.QRCodeEncoder.Params()
params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
enc = cv2.QRCodeEncoder.create(params)
mat = np.stack(enc.encode(code))          # 25 行 x 25 列 的二维码矩阵
img = (mat * 255 if mat.max() <= 1 else mat).astype(np.uint8)
scale = 12
big = cv2.resize(img, (img.shape[1] * scale, img.shape[0] * scale),
                 interpolation=cv2.INTER_NEAREST)
margin = 60
canvas = np.full((big.shape[0] + 2 * margin, big.shape[1] + 2 * margin), 255, np.uint8)
canvas[margin:margin + big.shape[0], margin:margin + big.shape[1]] = big
cv2.imwrite('test_qr.png', canvas)
print('test_qr.png saved:', canvas.shape, 'module count:', img.shape)

# 用 demo 的解码函数验证
res = m.decode_qr(cv2.imread('test_qr.png'))
print('decode result:', res)
assert any(d == code for d, _ in res), '解码失败!'
print('DECODE OK')
