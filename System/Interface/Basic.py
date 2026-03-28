import re
import random
import string

from loguru import logger

from PyQt5.QtGui import (
    QPainter,
    QTransform,
    QFontMetrics,
)

from PyQt5.QtCore import (
    Qt,
    QSize,
    QEvent,
    QPoint,
    QTimer,
    pyqtSignal,
    pyqtProperty
)

from PyQt5.QtWidgets import (
    QLabel,
    QHBoxLayout,
    QPushButton
)

from System.Common import (
    Utils,
    Styles
)

from System.Services import Player

class Timer(QTimer):
    def __init__(
        self,
        interval:    int      = 1000,
        callback:    object   = None,
        single_shot: bool     = False,
        auto_start:  bool     = False,
        parent:      QTimer   = None,
    ) -> None:

        super().__init__(parent)

        self.setInterval(interval)
        self.setSingleShot(single_shot)

        if callback:
            self.timeout.connect(callback)

        if auto_start:
            self.start()

class GlitchyButton(QPushButton):
    glitch_started = pyqtSignal()

    def __init__(self, title: str, enable_glitch_sound: bool = True) -> None:
        super().__init__(title)

        self.glitch_timer  = Timer(
            24,
            self.glitch_step,
            parent = self
        )
        
        self.glitch_steps_left = 0

        self.original_pos  = None
        self.original_size = None

        self.enable_glitch_sound  = enable_glitch_sound
        self.original_button_text = super().text()

        self.glitch_timer.timeout.connect(self.glitch_step)

        self.setFont(Utils.NType(13))
        self.setFixedHeight(50)
        self.installEventFilter(self)

    def random_ass_text(self, length: int) -> str:
        chars = string.ascii_letters + string.digits
        return "".join(random.choices(chars, k = length))
    
    def glitch_step(self) -> None:
        if self.glitch_steps_left <= 0:
            self.move(self.original_pos)
            self.resize(self.original_size)
            self.setText(self.original_button_text)
            
            self.glitch_timer.stop()
            
            return

        font_metrics    = QFontMetrics(self.font())
        estimated_length = self.width() // font_metrics.averageCharWidth()
        estimated_length = max(1, min(200, estimated_length))

        self.setText(self.random_ass_text(estimated_length))

        delta_x = random.randint(-3, 3)
        delta_y = random.randint(-4, 4)
        delta_w = random.randint(-4, 4)
        delta_h = random.randint(-2, 2)

        self.move(self.original_pos + QPoint(delta_x, delta_y))
        self.resize(
            QSize(
                max(10, self.original_size.width()  + delta_w),
                max(10, self.original_size.height() + delta_h),
            )
        )

        self.glitch_steps_left -= 1

    def start_glitch(self) -> None:
        if self.enable_glitch_sound:
            Player.ui_player.play_sound("Reject")

        self.glitch_started.emit()

        if self.glitch_timer.isActive():
            return

        self.original_pos  = self.pos()
        self.original_size = self.size()
        self.setFixedSize(self.original_size)

        self.glitch_steps_left = 7
        self.glitch_timer.start()
    
    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj == self and event.type() == QEvent.MouseButtonPress:
            if not self.isEnabled():
                self.start_glitch()
                return True

        return super().eventFilter(obj, event)

class NothingButton(GlitchyButton):
    def __init__(self, title: str, enable_glitch_sound: bool = True) -> None:
        super().__init__(title, enable_glitch_sound)
        self.setStyleSheet(Styles.Buttons.nothing_styled_button)

class Button(GlitchyButton):
    def __init__(self, title: str, enable_glitch_sound: bool = True) -> None:
        super().__init__(title, enable_glitch_sound)
        self.setStyleSheet(Styles.Buttons.normal_button)

class ButtonWithOutline(GlitchyButton):
    def __init__(self, title: str, enable_glitch_sound: bool = True) -> None:
        super().__init__(title, enable_glitch_sound)
        self.setStyleSheet(Styles.Buttons.normal_button_with_border)

class ButtonWithOutlineSlim(GlitchyButton):
    def __init__(self, title: str, enable_glitch_sound: bool = True) -> None:
        super().__init__(title, enable_glitch_sound)
        self.setStyleSheet(Styles.Buttons.normal_button_with_border_slim)
        self.setFixedHeight(35)

class ButtonRow(QHBoxLayout):
    def __init__(
        self,
        buttons: list[tuple],
        spacing: int = 10,
    ) -> None:
        
        super().__init__()

        self.setSpacing(spacing)
        self.buttons: dict[str, GlitchyButton] = {}

        for item in buttons:
            if len(item) == 4:
                class_name, text, callback, glitch = item
            
            else:
                class_name, text, callback = item
                glitch = True

            btn = class_name(text, enable_glitch_sound=glitch)
            btn.clicked.connect(callback)

            self.addWidget(btn)
            self.buttons[text] = btn

    def get_button(self, text: str) -> GlitchyButton | None:
        return self.buttons.get(text)

class TitleLabel(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)

        self._scale:    float = 1.0
        self._rotation: float = 0.0

        self.setContentsMargins(0, 0, 0, 5)
        self.setFont(Utils.NType(15))
        self.setStyleSheet(Styles.Other.font)

        self.original_text: str       = text
        self.display_text:  list[str] = list(text)
        self.solved_indices: set[int] = set()

        self.chars = string.ascii_uppercase

        self.glitch_timer = Timer(
            24,
            self.text_glitch_step,
            parent = self
        )

    def start_glitch(self) -> None:
        self.solved_indices.clear()
        self.glitch_timer.start()

    def text_glitch_step(self) -> None:
        new_text = []

        for i, char in enumerate(self.original_text):
            if i in self.solved_indices or char == " ":
                new_text.append(char)
            
            elif random.random() < 0.4:
                self.solved_indices.add(i)
                new_text.append(char)
            
            else:
                new_text.append(random.choice(self.chars))

        self.setText("".join(new_text))

        if len(self.solved_indices) >= len(self.original_text.replace(" ", "")):
            self.setText(self.original_text)
            self.glitch_timer.stop()

    @pyqtProperty(float)
    def scale(self) -> float:
        return self._scale

    @scale.setter
    def scale(self, value: float) -> None:
        self._scale = value
        self.update()

    @pyqtProperty(float)
    def rotation(self) -> float:
        return self._rotation

    @rotation.setter
    def rotation(self, value: float) -> None:
        self._rotation = value
        self.update()

    def paintEvent(self, event: QEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect      = self.contentsRect()
        alignment = self.alignment()
        cx        = rect.width()  / 2.0
        cy        = rect.height() / 2.0

        if alignment & Qt.AlignmentFlag.AlignLeft:
            px = 0.0
        
        elif alignment & Qt.AlignmentFlag.AlignRight:
            px = float(rect.width())
        
        else:
            px = cx

        transform = QTransform()
        transform.translate(px, cy)
        transform.scale(self._scale, self._scale)
        transform.translate(-px, -cy)
        transform.translate(cx, cy)
        transform.rotate(self._rotation)
        transform.translate(-cx, -cy)

        painter.setTransform(transform)
        painter.setPen(self.palette().windowText().color())
        painter.setFont(self.font())

        text_flags = alignment | (Qt.TextFlag.TextWordWrap if self.wordWrap() else 0)
        painter.drawText(rect, text_flags, self.text())
        painter.end()

class DescriptionLabel(QLabel):
    def __init__(
            self,
            text: str,
            maximum_width: int | None = None
        ) -> None:
        
        text = re.sub(r"`([^`]*)`", r'<span style="color:white;">\1</span>', text)
        text = text.replace("\n", "<br>")

        super().__init__(text)

        self.setFont(Utils.NType(12))
        self.setStyleSheet(Styles.Other.second_font)
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)

        if maximum_width:
            self.setMaximumWidth(maximum_width)

class Image(QLabel):
    clicked = pyqtSignal()

    def __init__(self, pixmap: object) -> None:
        super().__init__()

        self.setCursor(Qt.PointingHandCursor)
        self.update_image(pixmap)

    def update_image(self, pixmap: object) -> None:
        self.setPixmap(pixmap)

    def mousePressEvent(self, event: QEvent) -> None:
        self.clicked.emit()