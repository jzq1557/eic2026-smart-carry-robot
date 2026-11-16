import cv2

print('--- camera scan ---')
found = []
for i in range(6):
    cap = cv2.VideoCapture(i)
    ok, frame = cap.read()
    if ok:
        cv2.imwrite(f'cam{i}.jpg', frame)
        print(f'cam {i}: OK  -> saved cam{i}.jpg')
        found.append(i)
    cap.release()

print('--- result ---')
if len(found) == 2:
    print(f'two cameras found: index {found[0]} and {found[1]}')
    print('open cam*.jpg to tell which is FRONT (qr) / which is DOWN (floor)')
elif len(found) == 1:
    print(f'only ONE camera found: index {found[0]} - check the other USB plug')
else:
    print('NO camera found - check USB connections')
