import sys
import cv2
import time
import numpy as np
import os
import logging
import serial
import serial.tools.list_ports
import threading
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QSlider, QPushButton,
                             QTextEdit, QFrame, QComboBox, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QThread
from PyQt6.QtGui import QImage, QPixmap
from ultralytics import YOLO

# --- CONFIGURATION ---
MODEL_PATH = r"runs\classify\3d_print_defect_cls_3dv2\weights\best.pt"
BAUD_RATE = 115200


class CameraThread(QThread):
    """Поток обработки видеоданных и инференса YOLOv8"""
    frame_ready = pyqtSignal(np.ndarray, str, float)

    def __init__(self, model_path):
        super().__init__()
        try:
            self.model = YOLO(model_path)
            self._run_flag = True
            self.zoom_factor = 1.0
            self._zoom_lock = threading.Lock()

            # Счётчик FPS
            self.fps = 0.0
            self.frame_count = 0
            self.last_time = time.time()
        except Exception as e:
            print(f"❌ Ошибка загрузки модели: {e}")
            self._run_flag = False

    def set_zoom(self, zoom_factor):
        """Потокобезопасное обновление коэффициента зума"""
        with self._zoom_lock:
            self.zoom_factor = zoom_factor

    def _apply_center_crop(self, frame, zoom_factor, target_size=320):
        """Применение квадратного центрального кропа без искажений"""
        h, w = frame.shape[:2]
        min_side = min(h, w)

        if zoom_factor > 1.0:
            crop_size = int(min_side / zoom_factor)
            crop_size = max(crop_size, 32)
            start_y = (h - crop_size) // 2
            start_x = (w - crop_size) // 2
            cropped = frame[start_y:start_y + crop_size, start_x:start_x + crop_size]
        else:
            start_y = (h - min_side) // 2
            start_x = (w - min_side) // 2
            cropped = frame[start_y:start_y + min_side, start_x:start_x + min_side]

        return cv2.resize(cropped, (target_size, target_size))

    def run(self):
        if not self._run_flag:
            return

        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        for _ in range(5):
            cap.read()

        frame_idx = 0
        while self._run_flag:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            frame_idx += 1
            # Пропуск каждого 2-го кадра для разгрузки CPU
            if frame_idx % 2 != 0:
                time.sleep(0.005)
                continue

            # Предобработка: grayscale + дублирование канала
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray_3ch = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

            # Применение зум-кропа перед инференсом
            with self._zoom_lock:
                zoom = self.zoom_factor
            model_input = self._apply_center_crop(gray_3ch, zoom, target_size=320)

            try:
                results = self.model(model_input, verbose=False, imgsz=320)
                probs = results[0].probs
                top1_name = self.model.names[probs.top1]
                top1_conf = probs.top1conf.item()
            except Exception:
                top1_name = "Model Error"
                top1_conf = 0.0

            # Визуализация квадратной ROI при зуме > 1.0
            if zoom > 1.0:
                h_disp, w_disp = gray_3ch.shape[:2]
                min_side_disp = min(h_disp, w_disp)
                crop_size_disp = int(min_side_disp / zoom)
                crop_size_disp = max(crop_size_disp, 32)
                start_y_disp = (h_disp - crop_size_disp) // 2
                start_x_disp = (w_disp - crop_size_disp) // 2
                cv2.rectangle(gray_3ch, (start_x_disp, start_y_disp),
                              (start_x_disp + crop_size_disp, start_y_disp + crop_size_disp),
                              (0, 255, 0), 2)

            # Обновление FPS
            self.frame_count += 1
            if time.time() - self.last_time >= 1.0:
                self.fps = self.frame_count
                self.frame_count = 0
                self.last_time = time.time()

            # Наложение текста с классом, уверенностью и FPS
            display_text = f"{top1_name}: {top1_conf:.2f} | FPS: {self.fps:.1f}"
            cv2.putText(gray_3ch, display_text, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

            # Эмитим копию кадра для безопасности потоков
            self.frame_ready.emit(gray_3ch.copy(), top1_name, top1_conf)
            time.sleep(0.01)

        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()


class PrinterSerial:
    """Модуль последовательного взаимодействия с 3D-принтером"""

    def __init__(self):
        self.ser = None
        self.port = None

    def find_kobra_port(self):
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            desc = (f"{p.description} {p.manufacturer or ''}").upper()
            if any(chip in desc for chip in ['CH340', 'FTDI', 'CP210', 'CDC-ACM', 'USB SERIAL', 'SILICON LABS']):
                return p.device
        return None

    def connect(self, port=None):
        if not port:
            port = self.find_kobra_port()
        if not port:
            return False, "Port not found"
        self.port = port
        try:
            self.ser = serial.Serial(self.port, BAUD_RATE, timeout=0.1)
            time.sleep(2)
            return True, f"Connected to {self.port}"
        except Exception as e:
            self.ser = None
            return False, str(e)

    def disconnect(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except:
            pass
        finally:
            self.ser = None

    def send_cmd(self, cmd):
        if not self.ser or not self.ser.is_open:
            return False
        try:
            self.ser.reset_input_buffer()
            self.ser.write(f"{cmd}\n".encode())
            return True
        except (serial.SerialException, OSError, AttributeError):
            self.ser = None
            return False

    def read_all(self):
        if not self.ser or not self.ser.is_open:
            return []
        try:
            if self.ser.in_waiting:
                return self.ser.read_all().decode(errors='ignore').splitlines()
        except (serial.SerialException, OSError, AttributeError):
            self.ser = None
        return []


class MainWindow(QMainWindow):
    """Главное окно приложения с графическим интерфейсом"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CORE-PRINT OS | Defect Monitor")
        self.resize(1100, 750)
        self.setStyleSheet("background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI';")

        self.threshold = 0.80
        self.zoom_factor = 1.0
        self.action_mode = "sound_stop"
        self.last_alert_time = 0
        self.alert_cooldown = 2.0
        self.print_paused = False

        self.printer = PrinterSerial()

        timestamp = time.strftime("%Y_%m_%d_%H_%M_%S")
        if not os.path.exists("logs"):
            os.makedirs("logs")
        log_filename = f"logs/core_print_log_{timestamp}.log"

        self.file_logger = logging.getLogger("CorePrintLogger")
        self.file_logger.setLevel(logging.INFO)
        if self.file_logger.handlers:
            self.file_logger.handlers.clear()
        fh = logging.FileHandler(log_filename, encoding="utf-8")
        fh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        self.file_logger.addHandler(fh)

        self.init_ui()

        self.cam_thread = CameraThread(MODEL_PATH)
        self.cam_thread.frame_ready.connect(self.on_frame)
        self.cam_thread.start()

        self.serial_timer = QTimer()
        self.serial_timer.timeout.connect(self.poll_serial)
        self.serial_timer.start(100)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # === ЛЕВАЯ ПАНЕЛЬ ===
        left_panel = QFrame()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet("background-color: #252526; border-right: 1px solid #3e3e42;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(15, 15, 15, 15)

        # Режим реакции
        left_layout.addWidget(QLabel("<b>📢 Действие при обнаружении:</b>", styleSheet="color: #ccc;"))
        self.action_combo = QComboBox()
        self.action_combo.addItems([
            "Звуковой сигнал на ПК",
            "Автоматическая остановка 3D принтера (M25)",
            "Звук + Остановка принтера",
            "Только логирование"
        ])
        self.action_combo.currentIndexChanged.connect(self.on_action_change)
        self.action_combo.setStyleSheet("""
            QComboBox { background: #333; padding: 8px; border-radius: 4px; color: white; } 
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #333; color: white; selection-background-color: #007acc; }
        """)
        left_layout.addWidget(self.action_combo)

        left_layout.addSpacing(25)

        # Ползунок порога уверенности
        left_layout.addWidget(QLabel("<b>🎚️ Порог вероятности:</b>", styleSheet="color: #ccc;"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(50)
        self.slider.setMaximum(100)
        self.slider.setValue(80)
        self.slider.valueChanged.connect(self.update_threshold)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #555; height: 6px; border-radius: 3px; } 
            QSlider::handle:horizontal { background: #007acc; width: 16px; border-radius: 8px; margin: -5px 0; }
            QSlider::sub-page:horizontal { background: #007acc; border-radius: 3px; }
        """)
        left_layout.addWidget(self.slider)

        self.thresh_label = QLabel("80%")
        self.thresh_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thresh_label.setStyleSheet("color: #aaa; font-size: 16px; font-weight: bold; margin: 5px 0;")
        left_layout.addWidget(self.thresh_label)

        # Ползунок цифрового зума
        left_layout.addSpacing(15)
        left_layout.addWidget(QLabel("<b>🔍 Цифровой зум (область анализа):</b>", styleSheet="color: #ccc;"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setMinimum(100)
        self.zoom_slider.setMaximum(200)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(self.update_zoom)
        self.zoom_slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #555; height: 6px; border-radius: 3px; } 
            QSlider::handle:horizontal { background: #00aa00; width: 16px; border-radius: 8px; margin: -5px 0; }
            QSlider::sub-page:horizontal { background: #00aa00; border-radius: 3px; }
        """)
        left_layout.addWidget(self.zoom_slider)

        self.zoom_label = QLabel("1.0x")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setStyleSheet("color: #aaa; font-size: 16px; font-weight: bold; margin: 5px 0;")
        left_layout.addWidget(self.zoom_label)

        left_layout.addStretch()

        # Статус принтера
        self.status_label = QLabel("PRINTER: DISCONNECTED")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #f44; font-weight: bold; margin-bottom: 10px;")
        left_layout.addWidget(self.status_label)

        # Кнопка переподключения
        conn_btn = QPushButton("🔄 Переподключить")
        conn_btn.clicked.connect(self.reconnect_printer)
        conn_btn.setStyleSheet(
            "QPushButton { background: #007acc; padding: 10px; border-radius: 4px; color: white; font-weight: bold; } QPushButton:hover { background: #005f9e; }")
        left_layout.addWidget(conn_btn)

        # Кнопка возобновления печати
        left_layout.addSpacing(10)
        self.resume_btn = QPushButton("▶️ Возобновить печать (M24)")
        self.resume_btn.setEnabled(False)
        self.resume_btn.clicked.connect(self.resume_print)
        self.resume_btn.setStyleSheet(
            "QPushButton { background: #28a745; padding: 10px; border-radius: 4px; color: white; font-weight: bold; } QPushButton:disabled { background: #555; } QPushButton:hover { background: #218838; }")
        left_layout.addWidget(self.resume_btn)

        # === ПРАВАЯ ПАНЕЛЬ ===
        right_content = QWidget()
        right_layout = QVBoxLayout(right_content)
        right_layout.setContentsMargins(10, 10, 10, 10)

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #000; border: 1px solid #444; border-radius: 4px;")
        self.video_label.setMinimumSize(400, 300)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout.addWidget(self.video_label, 4)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            "background-color: #0d0d0d; color: #0f0; font-family: 'Consolas', monospace; border: 1px solid #444; border-radius: 4px; padding: 5px; font-size: 12px;")
        right_layout.addWidget(self.console, 1)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_content)

        self.reconnect_printer()

    def on_action_change(self, index):
        modes = ["sound", "stop", "sound_stop", "log"]
        self.action_mode = modes[index]
        self.log(f"🔔 Режим: {self.action_combo.currentText()}")

    def update_threshold(self, value):
        self.threshold = value / 100.0
        self.thresh_label.setText(f"{value}%")

    def update_zoom(self, value):
        self.zoom_factor = value / 100.0
        self.zoom_label.setText(f"{self.zoom_factor:.1f}x")
        if hasattr(self, 'cam_thread'):
            self.cam_thread.set_zoom(self.zoom_factor)
        self.log(f"🔍 Зум изменён: {self.zoom_factor:.1f}x")

    def log(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.console.append(f"[{timestamp}] {text}")
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.console.setTextCursor(cursor)
        if hasattr(self, 'file_logger'):
            self.file_logger.info(text)

    def poll_serial(self):
        try:
            if self.printer.ser and self.printer.ser.is_open:
                lines = self.printer.read_all()
                for line in lines:
                    if line.strip():
                        self.log(f"📥 {line}")
                        if "resum" in line.lower() and self.print_paused:
                            self.print_paused = False
                            self.resume_btn.setEnabled(False)
                            self.log("🟢 Печать возобновлена (внешний сигнал)")
            if not self.printer.ser:
                self.status_label.setText("PRINTER: OFFLINE")
                self.status_label.setStyleSheet("color: #f44; font-weight: bold; margin-bottom: 10px;")
        except Exception as e:
            self.log(f"⚠️ Serial error: {e}")
            if self.printer:
                self.printer.ser = None
            self.status_label.setText("PRINTER: OFFLINE")
            self.status_label.setStyleSheet("color: #f44; font-weight: bold; margin-bottom: 10px;")

    def reconnect_printer(self):
        if self.printer.ser:
            self.printer.disconnect()
        success, msg = self.printer.connect()
        self.log(f"🔌 {msg}")
        if success:
            self.status_label.setText(f"PRINTER: {self.printer.port}")
            self.status_label.setStyleSheet("color: #0f0; font-weight: bold; margin-bottom: 10px;")
        else:
            self.status_label.setText("PRINTER: OFFLINE")
            self.status_label.setStyleSheet("color: #f44; font-weight: bold; margin-bottom: 10px;")

    @pyqtSlot(np.ndarray, str, float)
    def on_frame(self, cv_img, label, conf):
        h, w, ch = cv_img.shape
        qt_img = QImage(cv_img.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(qt_img).scaled(
            self.video_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))

        if label == "extrusion_error_print" and conf >= self.threshold:
            current_time = time.time()
            if current_time - self.last_alert_time > self.alert_cooldown:
                self.last_alert_time = current_time
                self.handle_defect(label, conf)

    def handle_defect(self, label, conf):
        mode = self.action_mode
        log_msg = f"⚠️ DEFECT: {label} ({conf:.1%})"

        if "sound" in mode:
            self.log(log_msg + " 🔊")
            try:
                import winsound
                winsound.Beep(850, 500)
            except:
                pass

        if "stop" in mode:
            if self.printer.send_cmd("M25"):
                self.log(log_msg + " 🛑 M25 Sent")
                self.print_paused = True
                self.resume_btn.setEnabled(True)
            else:
                self.log(log_msg + " ❌ Send Failed")

    def resume_print(self):
        if not self.print_paused:
            return
        self.log("▶️ Отправка команды возобновления: M24")
        if self.printer.send_cmd("M24"):
            self.log("✅ M24 accepted — печать возобновлена")
        else:
            self.log("❌ Ошибка отправки M24")
            return
        self.print_paused = False
        self.resume_btn.setEnabled(False)
        self.log("🟢 Система в режиме мониторинга")

    def closeEvent(self, event):
        self.cam_thread.stop()
        self.printer.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())