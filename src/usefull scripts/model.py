import torch
from ultralytics import YOLO

if __name__ == '__main__':
    model = YOLO("yolov8n-cls.pt")

    model.train(
        data=r"C:\Users\beloz\Desktop\3dv2.v1i.folder",
        epochs=20,
        imgsz=224,
        batch=512,
        workers=1,
        name="3d_print_defect_cls_3dv2",
        device=0
    )


