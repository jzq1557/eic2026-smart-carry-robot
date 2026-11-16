import cv2
import sys

CAM = int(sys.argv[1]) if len(sys.argv) > 1 else 2   # default: front cam

cap = cv2.VideoCapture(CAM)
print('FOCUS TOOL - twist the lens ring, make the score as HIGH as possible')
print('press q to quit')

best = 0.0
while True:
    ret, frame = cap.read()
    if not ret:
        continue
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    score = cv2.Laplacian(gray, cv2.CV_64F).var()   # sharpness score
    best = max(best, score)
    color = (0, 255, 0) if score > 100 else (0, 165, 255) if score > 50 else (0, 0, 255)
    cv2.putText(frame, f'sharpness: {score:.0f}', (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, color, 3)
    cv2.putText(frame, f'best: {best:.0f}', (10, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
    cv2.imshow('focus', frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
