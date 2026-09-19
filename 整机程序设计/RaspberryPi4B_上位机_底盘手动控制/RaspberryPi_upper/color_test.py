import cv2 as cv
import numpy as np
from models.Detection import Detection

det = Detection(1)
print('color view - press q to quit')
while True:
    ret, frame = det.cap.read()
    if not ret:
        continue
    hsv = cv.cvtColor(frame, cv.COLOR_BGR2HSV)
    for name, lo, hi, box in [('RED', det.lower_red, det.upper_red, (0,0,255)),
                              ('BLUE', det.lower_blue, det.upper_blue, (255,0,0)),
                              ('GREEN', det.lower_green, det.upper_green, (0,255,0))]:
        mask = cv.inRange(hsv, lo, hi)
        cnts, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            if cv.contourArea(c) > 800:
                x, y, w, h = cv.boundingRect(c)
                cv.rectangle(frame, (x, y), (x+w, y+h), box, 3)
                cv.putText(frame, name, (x, y-8), cv.FONT_HERSHEY_SIMPLEX, 0.9, box, 2)
    cv.imshow('color', frame)
    if cv.waitKey(1) & 0xFF == ord('q'):
        break
cv.destroyAllWindows()
