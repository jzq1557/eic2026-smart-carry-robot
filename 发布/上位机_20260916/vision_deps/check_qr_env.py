# -*- coding: utf-8 -*-
"""检查二维码识别相关库。"""
try:
    import cv2
    print('cv2', cv2.__version__)
    print('QRCodeDetector:', hasattr(cv2, 'QRCodeDetector'))
    print('detectAndDecodeMulti:', hasattr(cv2.QRCodeDetector(), 'detectAndDecodeMulti'))
except Exception as e:
    print('cv2 FAIL:', e)
try:
    from pyzbar import pyzbar
    print('pyzbar OK')
except Exception as e:
    print('pyzbar FAIL:', e)
try:
    import numpy
    print('numpy', numpy.__version__)
except Exception as e:
    print('numpy FAIL:', e)
