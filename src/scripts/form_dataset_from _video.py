import cv2
import os
import sys
import time
import platform

def get_input(prompt):
    return input(prompt).strip()

def open_folder(path):
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception as e:
        print(f"Не удалось открыть папку: {e}")

def main():
    video_path = get_input("Введите путь к видеофайлу: ")
    if not os.path.isfile(video_path):
        print("Файл не найден!")
        sys.exit(1)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("Не удалось открыть видеофайл!")
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    total_seconds = total_frames / fps if fps > 0 else 0

    if fps <= 0:
        print("Невозможно определить FPS видео.")
        cap.release()
        sys.exit(1)

    print(f"Видео загружено. FPS: {fps:.2f}, Длительность: {total_seconds:.2f} секунд")

    start_str = get_input("С какой секунды начинать разбивку? (если с начала — введите '-'): ")
    end_str = get_input("До какой секунды разбивать? (если до конца — введите '-'): ")

    start_sec = 0.0 if start_str == '-' else float(start_str)
    end_sec = total_seconds if end_str == '-' else float(end_str)

    if start_sec < 0 or end_sec > total_seconds or start_sec >= end_sec:
        print("Некорректный временной интервал!")
        cap.release()
        sys.exit(1)

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, video_name)
    os.makedirs(output_dir, exist_ok=True)

    start_frame = int(round(start_sec * fps))
    end_frame = min(int(round(end_sec * fps)), total_frames)

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    current_frame_idx = start_frame
    saved_files = []  # список имён файлов (только basename)

    print(f"Извлечение кадров с {start_sec:.2f} по {end_sec:.2f} секунд...")

    while current_frame_idx < end_frame:
        ret, frame = cap.read()
        if not ret:
            break

        timestamp_sec = current_frame_idx / fps
        sec_int = int(timestamp_sec)
        local_frame_num = current_frame_idx - start_frame

        filename = f"cap_{video_name}_{sec_int}_sec-{local_frame_num}-img.jpg"
        filepath = os.path.join(output_dir, filename)
        success = cv2.imwrite(filepath, frame)
        if not success:
            print(f"Предупреждение: не удалось сохранить {filename}")
        else:
            saved_files.append(filename)  # сохраняем только имя, не путь

        current_frame_idx += 1

    cap.release()
    print(f"Сохранено {len(saved_files)} кадров в папку: {output_dir}")

    print("Через 3 секунды откроется папка с кадрами...")
    time.sleep(3)
    open_folder(output_dir)

    defect_filename = get_input(
        "\nВведите имя файла, в котором впервые виден дефект (например: cap_new_1_sec-54-img.jpg): "
    ).strip()

    if not defect_filename:
        print("Имя не указано. Переименование отменено.")
        return

    if defect_filename not in saved_files:
        print(f"Ошибка: файл '{defect_filename}' не найден среди сохранённых кадров.")
        print("Список первых 5 файлов:")
        for f in saved_files[:5]:
            print(f"  {f}")
        print("...")
        return

    defect_index = saved_files.index(defect_filename)

    # Теперь переименовываем
    for i, old_name in enumerate(saved_files):
        old_path = os.path.join(output_dir, old_name)
        sec_part = old_name.split('_')[-3]  # например, '1'
        frame_part = old_name.split('-')[-2]  # например, '54'

        if i < defect_index:
            new_name = f"clean_{sec_part}_sec-{frame_part}-img.jpg"
        else:
            new_name = f"defect_{sec_part}_sec-{frame_part}-img.jpg"

        new_path = os.path.join(output_dir, new_name)

        if os.path.exists(old_path):
            os.rename(old_path, new_path)
        else:
            print(f"Предупреждение: файл {old_name} уже отсутствует — пропускаем.")

    print(f"\nПереименовано: {defect_index} кадров как 'clean', {len(saved_files) - defect_index} как 'defect'.")

if __name__ == "__main__":
    main()