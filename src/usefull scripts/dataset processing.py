import os
import numpy as np
from PIL import Image

def grayscale_to_rgb_pil(img_path):
    img = Image.open(img_path).convert('RGB')
    # Центральный квадратный кроп
    w, h = img.size
    min_side = min(w, h)
    left = (w - min_side) // 2
    top = (h - min_side) // 2
    right = left + min_side
    bottom = top + min_side
    img_cropped = img.crop((left, top, right, bottom))  # квадрат

    # Конвертация в grayscale
    img_np = np.array(img_cropped, dtype=np.float32)
    img = img_cropped.convert('L')  # → grayscale (1 канал)
    img = img.convert('RGB')  # → дублируем канал: (H, W, 3)
    return img

def preprocess_single_folder(folder_path):
    supported_ext = ('.png', '.jpg', '.jpeg')
    image_files = [
        f for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f)) and f.lower().endswith(supported_ext)
    ]
    print(f"Найдено {len(image_files)} изображений в {folder_path}")
    for fname in image_files:
        img_path = os.path.join(folder_path, fname)
        try:
            processed_img = grayscale_to_rgb_pil(img_path)
            processed_img.save(img_path)  # перезапись
            print(f"Обработано: {fname}")
        except Exception as e:
            print(f"Ошибка при обработке {fname}: {e}")

if __name__ == '__main__':
    folder_to_process = r"C:\Users\beloz\Desktop\task last\test2"
    preprocess_single_folder(folder_to_process)
    print("Предобработка завершена.")
