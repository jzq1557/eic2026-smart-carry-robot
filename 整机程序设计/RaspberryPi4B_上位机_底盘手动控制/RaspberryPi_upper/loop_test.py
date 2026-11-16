from models.Detection import Detection
from models.MoveControl import MoveControl

# camera index: check with 'ls /dev/video*' first, edit if drifted
det = Detection(1)
mc = MoveControl('/dev/ttyUSB0', 115200)

mc.wait_for_start_cmd()
print('[1/4] link OK (wrist nodded)')

print('[2/4] scanning QR... show the 123+321 code to camera')
qr = det.get_qr_info(data_len=7)
print('[2/4] QR =', qr)

mc.send_qr_info(qr)
print('[3/4] QR sent to STM32')

mc.grubBlockFromRawArea()
print('[4/4] grab done - FULL LOOP OK')
