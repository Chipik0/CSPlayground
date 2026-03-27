import sys

from PyQt5.QtCore import (
    Qt,
    QTimer
)

from PyQt5.QtGui import (
    QColor,
    QPainter,
    QPaintEvent,
    QWheelEvent,
    QResizeEvent,
    QLinearGradient
)

from PyQt5.QtWidgets import (
    QLabel,
    QWidget,
    QLineEdit,
    QCheckBox,
    QVBoxLayout,
    QPushButton,
    QScrollArea,
    QApplication
)

SPRING_STIFFNESS           = 0.04
FADE_OVERLAY_SIZE          = 60
SPRING_DAMPING_FACTOR      = 0.4
ANIMATION_TICK_INTERVAL    = 8
USER_SCROLL_IDLE_TIMEOUT   = 150
WHEEL_SCROLL_SENSITIVITY   = 1.0
INERTIA_DECELERATION_RATE  = 0.93
VISUAL_RESISTANCE_STRENGTH = 600.0

class ContentCanvas(QWidget):
    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)

        self.layout_manager = QVBoxLayout(self)
        self.layout_manager.setContentsMargins(20, 20, 20, 20)
        self.layout_manager.setSpacing(20)
        self.layout_manager.setSizeConstraint(QVBoxLayout.SetFixedSize)

class ElasticScrollArea(QScrollArea):
    def __init__(
        self,
        background: QColor = QColor(30, 30, 30),
        parent:     QWidget = None
    ) -> None:
        
        super().__init__(parent)

        self.raw_scroll_position = 0.0
        self.velocity_speed      = 0.0
        self.scrolling_is_active = False

        self.setFrameStyle(0)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.setStyleSheet(f"background-color: {background.name()}; border: none;")

        self.canvas = ContentCanvas(self.viewport())

        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self.handle_scroll_finished)

        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(ANIMATION_TICK_INTERVAL)
        self.animation_timer.timeout.connect(self.process_animation_tick)

        self.viewport().installEventFilter(self)

    def add_widget(self, widget: QWidget) -> None:
        self.canvas.layout_manager.addWidget(widget)
        self.update_canvas_geometry()

    def update_canvas_geometry(self) -> None:
        hint = self.canvas.layout_manager.sizeHint()
        
        self.canvas.resize(
            self.viewport().width(),
            hint.height()
        )

    def handle_scroll_finished(self) -> None:
        self.scrolling_is_active = False

    def calculate_maximum_scroll(self) -> float:
        return float(max(0, self.canvas.height() - self.viewport().height()))

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta_vertical = event.angleDelta().y()
        maximum_limit  = self.calculate_maximum_scroll()
        resistance     = 1.0

        if self.raw_scroll_position < 0 or self.raw_scroll_position > maximum_limit:
            excess      = abs(self.raw_scroll_position) if self.raw_scroll_position < 0 else self.raw_scroll_position - maximum_limit
            resistance  = max(0.05, 1.0 / (1.0 + excess / (VISUAL_RESISTANCE_STRENGTH * 0.5)))
            resistance *= 0.3

        increment = (delta_vertical * WHEEL_SCROLL_SENSITIVITY / 8.0) * resistance
        
        if (self.velocity_speed > 0 and increment > 0) or (self.velocity_speed < 0 and increment < 0):
            self.velocity_speed *= 0.5

        self.velocity_speed     -= increment
        self.scrolling_is_active = True
        
        self.idle_timer.start(USER_SCROLL_IDLE_TIMEOUT)

        if not self.animation_timer.isActive():
            self.animation_timer.start()
        
        event.accept()

    def process_animation_tick(self) -> None:
        maximum_limit = self.calculate_maximum_scroll()
        
        self.raw_scroll_position += self.velocity_speed
        
        overshoot = 0.0

        if self.raw_scroll_position < 0.0:
            overshoot = self.raw_scroll_position
        
        if self.raw_scroll_position > maximum_limit:
            overshoot = self.raw_scroll_position - maximum_limit

        if overshoot == 0.0:
            self.velocity_speed *= INERTIA_DECELERATION_RATE
        
        if overshoot != 0.0 and self.scrolling_is_active:
            self.velocity_speed *= 0.80
        
        if overshoot != 0.0 and not self.scrolling_is_active:
            spring_force  = -overshoot * SPRING_STIFFNESS
            damping_force = -self.velocity_speed * SPRING_DAMPING_FACTOR
            self.velocity_speed += (spring_force + damping_force)

        self.apply_content_position()
        
        if not abs(self.velocity_speed) < 0.01 or (self.scrolling_is_active or abs(overshoot) < 0.1):
            return
        
        if abs(overshoot) > 0.1:
            return
        
        self.raw_scroll_position = max(0.0, min(maximum_limit, self.raw_scroll_position))
        self.apply_content_position()

        self.animation_timer.stop()

    def apply_content_position(self) -> None:
        maximum_limit = self.calculate_maximum_scroll()
        raw           = self.raw_scroll_position
        visual_y      = raw

        if raw < 0.0:
            visual_y = raw / (1.0 + abs(raw) / VISUAL_RESISTANCE_STRENGTH)
        
        if raw > maximum_limit:
            excess   = raw - maximum_limit
            visual_y = maximum_limit + (excess / (1.0 + excess / VISUAL_RESISTANCE_STRENGTH))

        self.canvas.move(0, int(-visual_y))
        
        self.viewport().update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        
        painter          = QPainter(self.viewport())
        background_color = self.palette().color(self.backgroundRole())
        
        top_gradient = QLinearGradient(
            0, 0,
            0, FADE_OVERLAY_SIZE
        )
        top_gradient.setColorAt(0.0, background_color)
        top_gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        
        bottom_gradient = QLinearGradient(
            0, self.viewport().height(), 
            0, self.viewport().height() - FADE_OVERLAY_SIZE
        )
        bottom_gradient.setColorAt(0.0, background_color)
        bottom_gradient.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.fillRect(
            0, 
            0, 
            self.viewport().width(), 
            FADE_OVERLAY_SIZE, 
            top_gradient
        )
        
        painter.fillRect(
            0, 
            self.viewport().height() - FADE_OVERLAY_SIZE, 
            self.viewport().width(), 
            FADE_OVERLAY_SIZE, 
            bottom_gradient
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update_canvas_geometry()

class DemoWindow(QWidget):
    # Initialization
    def __init__(self) -> None:
        super().__init__()
        
        self.setWindowTitle("Universal Elastic Scroll")
        
        self.resize(400, 600)
        
        self.setStyleSheet("background-color: #141419;")

        self.main_layout = QVBoxLayout(self)
        
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = ElasticScrollArea(QColor(20, 20, 25))
        
        self.main_layout.addWidget(self.scroll_area)

        self.populate_content()


    # Logic functions
    def populate_content(self) -> None:
        for index in range(15):
            self.scroll_area.add_widget(
                QLabel(f"<b style='color: white;'>Section {index + 1}</b>")
            )
            
            button = QPushButton(f"Interactive Button {index + 1}")
            
            button.setCursor(Qt.PointingHandCursor)
            
            button.setStyleSheet("""
                QPushButton { 
                    background-color: #3d3d46; color: white; border-radius: 5px; padding: 10px; 
                }
                QPushButton:hover { background-color: #4d4d56; }
            """)
            
            self.scroll_area.add_widget(button)

            edit_field = QLineEdit()
            
            edit_field.setPlaceholderText(f"Input field {index + 1}...")
            
            edit_field.setStyleSheet("background: #2d2d32; color: white; border: 1px solid #444; padding: 5px;")
            
            self.scroll_area.add_widget(edit_field)
            
            self.scroll_area.add_widget(
                QCheckBox("Check this option", styleSheet = "color: gray;")
            )


if __name__ == "__main__":
    application = QApplication(sys.argv)
    
    window = DemoWindow()
    
    window.show()
    
    sys.exit(application.exec_())