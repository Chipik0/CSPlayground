import os
import sys
import time
import random
import traceback

from datetime import (
    datetime
)

from System.Interface import (
    Basic
)

start_time = time.perf_counter()

from loguru import (
    logger,
)

from PyQt5.QtCore import (
    Qt,
    QRect,
    QTimer,
    pyqtSlot,
    QSettings,
    pyqtSignal,
    pyqtProperty,
    QEasingCurve,
    QPropertyAnimation
)

from PyQt5.QtGui import (
    QIcon,
    QFont,
    QColor,
    QPixmap,
    QPainter,
    QFontMetrics,
    QFontDatabase,
    QSurfaceFormat
)

from PyQt5.QtWidgets import (
    QWidget,
    QMainWindow,
    QApplication,
    QStackedWidget,
    QGraphicsOpacityEffect
)

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS

else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

from System.Common import (
    Utils,
    Styles
)

from System.Services import (
    Player
)

from System.Interface import (
    Windows
)

from System.Common.Constants import (
    SettingsDict,
    load_settings,
    CurrentSettings,
    prepare_default_settings,
)

from System.Views.ProjectMenu import (
    MainMenu
)

from System.Views import (
    Compositor
)

processing_exception = False

def handle_exception(
        exc_type:      type,
        exc_value:     BaseException,
        exc_traceback: object
    ) -> None:

    global processing_exception

    if processing_exception:
        return

    processing_exception = True

    try:
        error_message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        logger.error(f"Uncaught exception: {error_message}")

        title = "Panic: " + str(exc_value) if random.random() > 0.005 else "0x000000DEAD"
        Windows.ErrorWindow(title, f"{error_message}\nCassette will now close.", "No way").exec_()

    except Exception as error:
        logger.critical(f"Critical failure in error handler: {error}")

    finally:
        processing_exception = False

sys.excepthook = handle_exception

class EasterEggManager:
    CHANCE       = 0.005
    STARTUP_DATA = [
        {"content": "System/Assets/Image/Anomaly.png", "sound": "Packs/NOK/Anomaly", "duration": 7500, "fade": 200},
        {"content": "System/Assets/Image/IEYTD2.png", "scale": False},
        {"content": "First, there was The Void"},
        {"content": "The best of the best, still die like the rest"},
        {"content": "The cake is a lie"},
    ]

    def __init__(self, window: QMainWindow):
        self.window                  = window
        self.shake_history           = []
        self.shake_sound_count       = 0
        self.shake_direction         = 0
        self.shake_direction_changes = 0
        self.last_shake_x            = 0
        self.last_shake_time         = 0.0
        self.last_area               = window.width() * window.height()
        
        self.last_accordion_time     = 0.0
        self.resize_direction        = 0
        self.direction_changes       = 0
        self.last_change_time        = 0.0
        self.is_accordion_active     = False

        self.accordion_stop_timer = Basic.Timer(2000, self.stop_accordion_sound, True)
        self.shake_stop_timer     = Basic.Timer(2000, self.stop_shake_sound, True)

    def handle_shake(
            self,
            x: int,
            y: int
        ) -> None:

        now     = time.time()
        delta_x = x - self.last_shake_x

        self.last_shake_x = x

        if abs(delta_x) < 5:
            return

        current_direction = 1 if delta_x > 0 else -1

        if current_direction == self.shake_direction:
            return

        if now - self.last_shake_time > 0.8:
            self.shake_direction_changes = 0

        self.shake_direction          = current_direction
        self.shake_direction_changes += 1
        self.last_shake_time          = now

        if self.shake_direction_changes < 10:
            return

        self.shake_stop_timer.stop()
        self.shake_stop_timer.start()

        sound_index = min(5, self.shake_direction_changes // 2)
        if sound_index < 1:
            sound_index = random.randint(1, 5)

        Utils.ui_sound(f"Packs/NOK/Shake{sound_index}", random_spread = 0.35)
        self.shake_sound_count += 1

        if self.shake_sound_count > 50:
            logger.critical("Too much shaking! Emergency exit.")
            self.window.close()

    def handle_resize_accordion(
            self,
            width:  int,
            height: int
        ) -> None:

        now          = time.time()
        current_area = width * height
        delta_time   = (now - self.last_accordion_time) if self.last_accordion_time > 0 else 0.01
        area_diff    = current_area - self.last_area
        velocity     = abs(area_diff) / delta_time

        MIN_VELOCITY = 50000
        MAX_VELOCITY = 2000000

        if abs(area_diff) < 200 or velocity < MIN_VELOCITY:
            self.last_area           = current_area
            self.last_accordion_time = now
            return

        current_direction = 1 if area_diff > 0 else -1

        if current_direction != self.resize_direction:
            self.direction_changes += 1
            self.resize_direction   = current_direction

            if now - self.last_change_time > 1.0:
                self.direction_changes = 1

            self.last_change_time = now

            if self.direction_changes >= 10:
                self.is_accordion_active = True

        if self.is_accordion_active:
            self.accordion_stop_timer.stop()
            self.accordion_stop_timer.start()

            volume     = max(0.1, min(1.0, (velocity - MIN_VELOCITY) / (MAX_VELOCITY - MIN_VELOCITY)))
            sound_type = "Out" if current_direction > 0 else "In"
            Utils.ui_sound(f"Packs/NOK/Accordion{sound_type}", volume = volume)

        self.last_area           = current_area
        self.last_accordion_time = now

    def stop_accordion_sound(self) -> None:
        self.direction_changes   = 0
        self.is_accordion_active = False

    def stop_shake_sound(self) -> None:
        self.shake_sound_count       = 0
        self.shake_direction_changes = 0

    def check_calendar_events(self) -> None:
        now = datetime.now()

        if now.day == 8 and now.month == 6:
            Windows.ErrorWindow("Wow!", "Today is a vacuum cleaner day!").exec_()

        if now.day == 4 and now.month == 5:
            Windows.ErrorWindow("><", "my birthday").exec_()

    @staticmethod
    def is_image(content: str) -> bool:
        return content.lower().endswith('.png')

    @staticmethod
    def get_startup_egg() -> dict | None:
        if random.random() < EasterEggManager.CHANCE:
            return random.choice(EasterEggManager.STARTUP_DATA)
        return None


# Startup Overlay

class StartupFadeOverlay(QWidget):
    finished = pyqtSignal()

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self.bg_opacity_value = 1.0
        self.current_pixmap   = None
        self.current_text     = None
        self.text_rect        = None
        self.font             = None

        self.bg_anim = Utils.Animations.make_animation(
            self,
            [(0.0, 1.0), (1.0, 0.0)],
            b"bgOpacity",
            700,
            QEasingCurve.OutCubic,
        )
        self.bg_anim.finished.connect(self.on_bg_anim_finished)

    @pyqtProperty(float)
    def bgOpacity(self) -> float:
        return self.bg_opacity_value

    @bgOpacity.setter
    def bgOpacity(self, value: float) -> None:
        self.bg_opacity_value = float(value)
        self.update()

    def start(self, default_hold_ms: int = 600) -> None:
        overlay_start = time.perf_counter()

        self.setGeometry(self.parent().rect())
        self.show()

        is_first_start = CurrentSettings.get("new_user", True)
        wait_time      = default_hold_ms
        self.font      = Utils.NType(30 if is_first_start else 10)

        if is_first_start:
            self.current_text = "Get ready."
            self.text_rect    = self.rect()

            settings = QSettings("chips047", "Cassette")
            settings.setValue("new_user", False)
            settings.sync()
            load_settings()

            Utils.ui_sound("App/Startup")
            QTimer.singleShot(wait_time, self.bg_anim.start)
            logger.debug(f"Startup overlay configured in {(time.perf_counter() - overlay_start) * 1000:.2f}ms")
            return

        egg = EasterEggManager.get_startup_egg()

        if not egg:
            Utils.ui_sound("App/Startup", volume = 1.0)
            QTimer.singleShot(wait_time, self.bg_anim.start)
            return

        content   = egg["content"]
        wait_time = egg.get("duration", default_hold_ms)

        if EasterEggManager.is_image(content):
            pixmap = QPixmap(content)
            if egg.get("scale", True):
                self.current_pixmap = pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                self.current_pixmap = pixmap
        else:
            self.current_text = content
            metrics           = QFontMetrics(self.font)
            bounding_rect     = metrics.boundingRect(self.current_text)
            text_width        = bounding_rect.width() + 20
            text_height       = bounding_rect.height() + 20
            margin            = 80
            random_x          = random.randint(margin, max(margin, self.width() - text_width - margin))
            random_y          = random.randint(margin, max(margin, self.height() - text_height - margin))
            self.text_rect    = QRect(random_x, random_y, text_width, text_height)

        if "fade" in egg:
            self.bg_anim.setDuration(egg["fade"])

        if "sound" in egg:
            Utils.ui_sound(egg["sound"], 1.0)

        Utils.ui_sound("App/Startup")
        QTimer.singleShot(wait_time, self.bg_anim.start)
        logger.debug(f"Startup overlay configured in {(time.perf_counter() - overlay_start) * 1000:.2f}ms")

    def on_bg_anim_finished(self) -> None:
        self.close()
        self.finished.emit()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

        alpha = int(self.bg_opacity_value * 255)
        painter.fillRect(self.rect(), QColor(0, 0, 0, alpha))
        painter.setOpacity(self.bg_opacity_value)

        if self.current_pixmap and not self.current_pixmap.isNull():
            x = (self.width() - self.current_pixmap.width()) // 2
            y = (self.height() - self.current_pixmap.height()) // 2
            painter.drawPixmap(x, y, self.current_pixmap)
            return

        if self.current_text:
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(self.font)
            painter.drawText(self.text_rect, Qt.AlignCenter, self.current_text)


# Application Window

class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        window_init_start = time.perf_counter()

        self.ee_manager = EasterEggManager(self)
        self.is_closing = False

        self.setWindowTitle("Cassette")
        self.resize(1280, 800)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        logger.debug("Loading MainMenu and Compositor...")
        widget_load_start = time.perf_counter()

        self.main_menu_widget  = MainMenu(self)
        self.compositor_widget = Compositor.CompositorWidget(self)

        logger.debug(f"Widgets created in {(time.perf_counter() - widget_load_start) * 1000:.2f}ms")

        for widget, opacity in [(self.main_menu_widget, 1.0), (self.compositor_widget, 0.0)]:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(opacity)
            widget.setGraphicsEffect(effect)
            self.stack.addWidget(widget)

        self.main_menu_widget.composition_created.connect(self.show_compositor)
        self.compositor_widget.back_to_main_menu_requested.connect(self.show_main_menu)
        self.stack.setCurrentWidget(self.main_menu_widget)
        self.setStyleSheet(f"background-color: {Styles.Colors.background};")

        self.intro_overlay = StartupFadeOverlay(self)
        self.intro_overlay.finished.connect(self.ee_manager.check_calendar_events)

        self.setup_animations()

        logger.success(f"ApplicationWindow initialized in {(time.perf_counter() - window_init_start) * 1000:.2f}ms")

    def setup_animations(self) -> None:
        self.main_menu_fadeout = QPropertyAnimation(self.main_menu_widget.graphicsEffect(), b"opacity")
        self.main_menu_fadeout.setDuration(300)
        self.main_menu_fadeout.setStartValue(1.0)
        self.main_menu_fadeout.setEndValue(0.0)
        self.main_menu_fadeout.setEasingCurve(QEasingCurve.OutCubic)

        self.compositor_fadeout = QPropertyAnimation(self.compositor_widget.graphicsEffect(), b"opacity")
        self.compositor_fadeout.setDuration(300)
        self.compositor_fadeout.setStartValue(1.0)
        self.compositor_fadeout.setEndValue(0.0)
        self.compositor_fadeout.setEasingCurve(QEasingCurve.OutCubic)

        self.entry_move_animation = QPropertyAnimation(None, b"geometry")
        self.entry_move_animation.setDuration(700)
        self.entry_move_animation.setEasingCurve(QEasingCurve.OutElastic)

        self.entry_fade_animation = QPropertyAnimation(None, b"opacity")
        self.entry_fade_animation.setDuration(400)
        self.entry_fade_animation.setEasingCurve(QEasingCurve.OutCubic)

        self.main_menu_fadeout.finished.connect(self.on_transition_to_compositor)
        self.compositor_fadeout.finished.connect(self.on_transition_to_main_menu)

    def on_transition_to_compositor(self) -> None:
        self.main_menu_widget.setVisible(False)
        self.perform_widget_entry(self.compositor_widget)

    def on_transition_to_main_menu(self) -> None:
        self.compositor_widget.setVisible(False)
        self.compositor_widget.content_widget.unload_composition()
        self.perform_widget_entry(self.main_menu_widget)

    @pyqtSlot(object)
    def show_compositor(self, composition: object) -> None:
        self.compositor_widget.load_composition(composition)
        self.main_menu_fadeout.start()

    @pyqtSlot()
    def show_main_menu(self) -> None:
        self.compositor_fadeout.start()

    def perform_widget_entry(self, widget: QWidget) -> None:
        self.stack.setCurrentWidget(widget)
        widget.setVisible(True)

        rect = self.stack.geometry()
        self.entry_move_animation.setTargetObject(widget)
        self.entry_move_animation.setStartValue(QRect(rect.x(), rect.y() + 150, rect.width(), rect.height()))
        self.entry_move_animation.setEndValue(rect)

        self.entry_fade_animation.setTargetObject(widget.graphicsEffect())
        self.entry_fade_animation.setStartValue(0.0)
        self.entry_fade_animation.setEndValue(1.0)

        Utils.ui_sound("App/Eject")
        self.entry_move_animation.start()
        self.entry_fade_animation.start()

    def closeEvent(self, event: object) -> None:
        event.ignore()
        self.initiate_shutdown()

    def initiate_shutdown(self) -> None:
        if self.is_closing:
            return

        self.is_closing = True
        logger.info("Initiating shutdown sequence...")
        self.hide()

        content    = self.compositor_widget.content_widget
        multiplier = CurrentSettings.get("animation_multiplier", 1.0)

        if content.composition:
            content.composition.syncer.exit_app()

        if Player.player.is_playing and content.global_waveform_max > 1e-6:
            Player.player.tape(end_speed = 0.0, duration = 3.0, shutdown_on_finish = True)

            close_vis_timeout = int(multiplier * 1700)
            QTimer.singleShot(
                close_vis_timeout,
                lambda: (Utils.ui_sound("App/Close"), content.glyph_visualizer.exit(False))
            )
            QTimer.singleShot(close_vis_timeout + 1500, QApplication.instance().quit)
            return

        close_duration = 1800
        if content.composition:
            Utils.ui_sound("App/Close")
            content.glyph_visualizer.exit(False)

        QTimer.singleShot(int(close_duration * multiplier), QApplication.instance().quit)

# Entry Point

def main() -> None:
    prepare_default_settings(SettingsDict)
    load_settings()

    surface_format = QSurfaceFormat()
    surface_format.setVersion(4, 1)
    surface_format.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)

    if CurrentSettings.get("msaa"):
        surface_format.setSamples(CurrentSettings["msaa"])

    QSurfaceFormat.setDefaultFormat(surface_format)

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    for font_path in ["System/Assets/Fonts/NDot57.otf", "System/Assets/Fonts/NType82.otf"]:
        if QFontDatabase.addApplicationFont(font_path) == -1:
            logger.error(f"Failed to load font: {font_path}")

    icon_extension = {"win32": "ico", "darwin": "icns"}.get(sys.platform, "png")
    app.setWindowIcon(QIcon(f"System/Assets/Icons/Cassette/AppIcon.{icon_extension}"))

    main_window = ApplicationWindow()
    main_window.show()
    main_window.intro_overlay.start(670)

    total_load_time = (time.perf_counter() - start_time) * 1000
    logger.success(f"=== Total Startup Time: {total_load_time:.2f}ms ===")

    sys.exit(app.exec_())


if __name__ == '__main__':
    logger.debug(f"Main Process PID: {os.getpid()}")
    main()