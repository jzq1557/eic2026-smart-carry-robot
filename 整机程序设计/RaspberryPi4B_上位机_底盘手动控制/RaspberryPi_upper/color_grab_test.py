from models.Detection import Detection
from models.MoveControl import MoveControl

TARGET = 'red'   # change to 'blue' for the other test

det = Detection(1)
mc = MoveControl('/dev/ttyUSB0', 115200)
mc.wait_for_start_cmd()
print('[1/3] link OK')

print('[2/3] waiting for', TARGET, 'block (bottom-center, CLOSE to lens)...')
det.detect_color(TARGET)
print('[2/3]', TARGET, 'DETECTED!')

mc.grubBlockFromRawArea()
print('[3/3] grab done - COLOR TRIGGER LOOP OK')
