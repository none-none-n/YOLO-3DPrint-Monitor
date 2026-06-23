import cv2
import time
import numpy as np
from ultralytics import YOLO
print(2)
# Загрузка модели
model = YOLO(r"../runs\classify\3d_print_defect_cls_3dv2\weights\best.pt")

#runs\classify\3d_print_defect_cls_2243\weights/best.pt
# Инициализация камеры
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# ... после инициализации cap ...
# Прогрев камеры
for _ in range(5):
    cap.read()

last_log_time = time.time()

try:
    while True:
        ret, frame = cap.read()
        if not ret or frame is None or frame.size == 0 or frame.shape[0] == 0 or frame.shape[1] == 0:
            time.sleep(0.01)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_3ch = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

        start = time.perf_counter()
        results = model(gray_3ch, verbose=False)
        inference_ms = (time.perf_counter() - start) * 1000

        probs = results[0].probs
        top1_name = model.names[probs.top1]
        top1_conf = probs.top1conf.item()

        display_text = f"{top1_name}: {top1_conf:.2f}"
        cv2.putText(gray_3ch, display_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.imshow("3D Print Defect Classifier", gray_3ch)

        current_time = time.time()
        if current_time - last_log_time >= 2.0:
            print(f"{top1_name} {top1_conf:.2f}, {inference_ms:.1f}ms")
            last_log_time = current_time

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

except KeyboardInterrupt:
    pass
finally:
    time.sleep(0.1)
    cap.release()
    cv2.destroyAllWindows()
    time.sleep(0.1)
    cap.release()
