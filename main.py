# -*- coding: utf-8 -*-

import calendar
import ctypes
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTime,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


APP_VERSION = "1.0.1"
APP_NAME = "Salary Timer"
APP_DISPLAY_NAME = f"{APP_NAME} v{APP_VERSION}"
CONFIG_FILE = "config.json"
STARTUP_REG_NAME = "SalaryTimerWidget"
DEFAULT_WORKDAYS = 22
SECONDS_PER_PAY_DAY = 8 * 60 * 60
WINDOW_SIZE = QSize(166, 62)
COLLAPSED_SIZE = QSize(150, 50)
GITHUB_OWNER = "kouwenhao"
GITHUB_REPO = "salary-timer"
DEFAULT_QUOTES_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/quotes.json"
DEFAULT_UPDATE_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/update.json"
DEFAULT_DOWNLOAD_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest/download/SalaryTimer.exe"
QUOTES_CACHE_FILE = "quotes_cache.json"
QUOTE_SCHEDULER_TICK_MS = 60 * 1000
QUOTE_POPUP_DURATION_MS = 30 * 1000


def app_base_dir() -> Path:
    """返回配置文件所在目录：开发时为源码目录，打包后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def parse_hhmm(value: str, fallback: str = "09:00") -> dt_time:
    try:
        hour, minute = value.split(":", 1)
        return dt_time(int(hour), int(minute), 0)
    except Exception:
        hour, minute = fallback.split(":", 1)
        return dt_time(int(hour), int(minute), 0)


def time_to_hhmm(value: dt_time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


@dataclass
class AppConfig:
    monthly_salary: float = 10000.0
    workdays: int = DEFAULT_WORKDAYS
    manual_workdays: bool = False
    work_start: str = "09:00"
    work_end: str = "18:00"
    workdays_month: str = ""
    collapsed: bool = False
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    window_opacity: float = 0.82
    auto_start: bool = False
    display_mode: str = "day"
    usage_guide_shown: bool = False
    quotes_url: str = DEFAULT_QUOTES_URL
    update_url: str = DEFAULT_UPDATE_URL
    last_quote: str = ""
    quote_interval_minutes: int = 30

    @classmethod
    def from_dict(cls, data: Dict) -> "AppConfig":
        cfg = cls()
        for key in cfg.__dataclass_fields__:
            if key in data:
                setattr(cfg, key, data[key])
        cfg.monthly_salary = max(0.0, float(cfg.monthly_salary))
        cfg.workdays = max(1, min(31, int(cfg.workdays)))
        cfg.manual_workdays = bool(cfg.manual_workdays)
        cfg.collapsed = bool(cfg.collapsed)
        cfg.auto_start = bool(cfg.auto_start)
        cfg.usage_guide_shown = bool(cfg.usage_guide_shown)
        cfg.window_opacity = max(0.0, min(1.0, float(cfg.window_opacity)))
        if cfg.display_mode not in ("day", "month"):
            cfg.display_mode = "day"
        if not isinstance(cfg.quotes_url, str) or not cfg.quotes_url.strip():
            cfg.quotes_url = DEFAULT_QUOTES_URL
        if not isinstance(cfg.update_url, str) or not cfg.update_url.strip():
            cfg.update_url = DEFAULT_UPDATE_URL
        cfg.last_quote = str(cfg.last_quote or "")
        cfg.quote_interval_minutes = max(1, min(240, int(cfg.quote_interval_minutes)))
        parse_hhmm(cfg.work_start, "09:00")
        parse_hhmm(cfg.work_end, "18:00")
        return cfg

    def to_dict(self) -> Dict:
        return {
            "monthly_salary": self.monthly_salary,
            "workdays": self.workdays,
            "manual_workdays": self.manual_workdays,
            "work_start": self.work_start,
            "work_end": self.work_end,
            "workdays_month": self.workdays_month,
            "collapsed": self.collapsed,
            "window_x": self.window_x,
            "window_y": self.window_y,
            "window_opacity": self.window_opacity,
            "auto_start": self.auto_start,
            "display_mode": self.display_mode,
            "usage_guide_shown": self.usage_guide_shown,
            "quotes_url": self.quotes_url,
            "update_url": self.update_url,
            "last_quote": self.last_quote,
            "quote_interval_minutes": self.quote_interval_minutes,
        }


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AppConfig:
        if not self.path.exists():
            cfg = AppConfig()
            self.save(cfg)
            return cfg
        try:
            with self.path.open("r", encoding="utf-8") as fp:
                return AppConfig.from_dict(json.load(fp))
        except Exception:
            # 配置损坏时不让程序启动失败，直接回退到默认值并覆盖保存。
            cfg = AppConfig()
            self.save(cfg)
            return cfg

    def save(self, cfg: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fp:
            json.dump(cfg.to_dict(), fp, ensure_ascii=False, indent=2)


class StartupManager:
    """当前用户开机自启管理，使用 Windows Run 注册表项。"""

    REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

    @staticmethod
    def is_supported() -> bool:
        return sys.platform == "win32"

    @staticmethod
    def startup_command() -> str:
        if getattr(sys, "frozen", False):
            return f'"{Path(sys.executable).resolve()}"'

        pythonw = Path(sys.executable).with_name("pythonw.exe")
        runner = pythonw if pythonw.exists() else Path(sys.executable)
        script = Path(__file__).resolve()
        return f'"{runner}" "{script}"'

    @classmethod
    def is_enabled(cls) -> bool:
        if not cls.is_supported():
            return False
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.REG_PATH, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, STARTUP_REG_NAME)
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        if not cls.is_supported():
            raise RuntimeError("开机自启仅支持 Windows。")
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            cls.REG_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, cls.startup_command())
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_REG_NAME)
                except FileNotFoundError:
                    pass


class HolidayService:
    """timor.tech 节假日接口封装。

    type 字段约定：
    0 = 普通工作日，1 = 周末，2 = 法定节假日，3 = 调休补班。
    接口通常只返回节假日、周末和补班日期，普通工作日需要按周一到周五补齐。
    """

    API_URL = "https://timor.tech/api/holiday/year/{year}-{month:02d}?type=Y&week=Y"

    @classmethod
    def fetch_month_info(cls, year: int, month: int) -> Tuple[int, Dict[str, int]]:
        url = cls.API_URL.format(year=year, month=month)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read().decode("utf-8")
        payload = json.loads(raw)
        if payload.get("code") != 0:
            raise RuntimeError(f"timor API returned code={payload.get('code')}")

        day_types: Dict[str, int] = {}
        for day_key, item in payload.get("type", {}).items():
            try:
                day_types[day_key] = int(item.get("type"))
            except Exception:
                continue

        _, days_in_month = calendar.monthrange(year, month)
        workday_count = 0
        for day in range(1, days_in_month + 1):
            current = date(year, month, day)
            kind = day_types.get(current.isoformat())
            if kind in (0, 3):
                workday_count += 1
            elif kind in (1, 2):
                continue
            elif current.weekday() < 5:
                workday_count += 1

        return workday_count, day_types


class FetchSignals(QObject):
    finished = pyqtSignal(int, dict)
    failed = pyqtSignal(str)


class WorkdayFetchTask(QRunnable):
    def __init__(self, year: int, month: int):
        super().__init__()
        self.year = year
        self.month = month
        self.signals = FetchSignals()

    def run(self) -> None:
        try:
            days, day_types = HolidayService.fetch_month_info(self.year, self.month)
            self.signals.finished.emit(days, day_types)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class JsonFetchSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class JsonFetchTask(QRunnable):
    def __init__(self, url: str, timeout: int = 8):
        super().__init__()
        self.url = url
        self.timeout = timeout
        self.signals = JsonFetchSignals()

    def run(self) -> None:
        try:
            request = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8-sig")
            self.signals.finished.emit(json.loads(raw))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class RollingNumberWidget(QWidget):
    """平滑绘制金额数字，避免每秒刷新时出现随机抖动。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._from_amount = 0.0
        self._target_amount = 0.0
        self._display_amount = 0.0
        self._phase = 1.0
        self._started = time.monotonic()
        self._duration = 0.38
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, amount: float) -> None:
        next_amount = max(0.0, amount)
        if abs(next_amount - self._target_amount) < 0.005 and self._phase >= 1.0:
            return
        self._display_amount = self._current_display_amount()
        self._from_amount = self._display_amount
        self._target_amount = next_amount
        self._phase = 0.0
        self._started = time.monotonic()
        self._timer.start(16)
        self.update()

    def _advance(self) -> None:
        elapsed = time.monotonic() - self._started
        self._phase = min(1.0, elapsed / self._duration)
        self._display_amount = self._current_display_amount()
        if self._phase >= 1.0:
            self._display_amount = self._target_amount
            self._timer.stop()
        self.update()

    @staticmethod
    def _ease_out_cubic(value: float) -> float:
        return 1.0 - pow(1.0 - value, 3)

    def _current_display_amount(self) -> float:
        progress = self._ease_out_cubic(self._phase)
        return self._from_amount + (self._target_amount - self._from_amount) * progress

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = f"{self._display_amount:,.2f}"

        font_size = 24
        font = QFont("Comic Sans MS", font_size, QFont.Weight.Black)
        font.setStyleHint(QFont.StyleHint.Monospace)
        metrics = QFontMetricsF(font)
        while metrics.horizontalAdvance(text) > self.width() - 6 and font_size > 15:
            font_size -= 1
            font.setPointSize(font_size)
            metrics = QFontMetricsF(font)

        painter.setFont(font)
        text_width = metrics.horizontalAdvance(text)
        progress = min(1.0, self._phase)
        bounce = math.sin(progress * math.pi) * 1.2 if progress < 1.0 else 0.0
        x = max(0.0, self.width() - text_width - 3.0)
        baseline = (self.height() + metrics.ascent() - metrics.descent()) / 2.0 - bounce

        if progress < 1.0:
            ray_alpha = int(45 * math.sin(progress * math.pi))
            painter.setPen(QPen(QColor(43, 34, 25, ray_alpha), 1.4))
            right = self.width() - 7
            painter.drawLine(QPointF(right - 13, 6), QPointF(right - 4, 2))
            painter.drawLine(QPointF(right - 4, 18), QPointF(right + 5, 15))

        shadow_path = QPainterPath()
        shadow_path.addText(QPointF(x + 1.6, baseline + 2.2), font, text)
        painter.fillPath(shadow_path, QColor(73, 45, 24, 70))

        path = QPainterPath()
        path.addText(QPointF(x, baseline), font, text)
        outline_pen = QPen(QColor(42, 34, 26), 3.8)
        outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, outline_pen)
        painter.fillPath(path, QColor(255, 214, 67))

        shine_path = QPainterPath()
        shine_path.addText(QPointF(x - 0.6, baseline - 1.2), font, text)
        painter.strokePath(shine_path, QPen(QColor(255, 250, 205, 130), 0.9))


@dataclass
class CoinParticle:
    pos: QPointF
    velocity: QPointF
    radius: float
    life: float
    max_life: float
    spin: float
    gravity: float = 0.018
    squash: float = 1.0
    floor_y: Optional[float] = None


class CoinParticleOverlay(QWidget):
    """金额附近的金币飘出和下落粒子，不接收鼠标事件。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.particles = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def sparkle(self, origin: QPointF, amount: int = 3) -> None:
        for _ in range(amount):
            angle = random.uniform(math.radians(205), math.radians(335))
            speed = random.uniform(0.7, 1.7)
            self.particles.append(
                CoinParticle(
                    pos=QPointF(origin.x() + random.uniform(-8, 10), origin.y() + random.uniform(-5, 5)),
                    velocity=QPointF(math.cos(angle) * speed, math.sin(angle) * speed - 0.7),
                    radius=random.uniform(4.8, 7.0),
                    life=random.uniform(0.9, 1.3),
                    max_life=1.3,
                    spin=random.uniform(0, 360),
                    gravity=0.018,
                    squash=random.uniform(0.85, 1.0),
                    floor_y=None,
                )
            )
        self.update()

    def drop(self, left: float, right: float, floor_y: float, amount: int = 1) -> None:
        for _ in range(amount):
            life = random.uniform(1.55, 2.05)
            self.particles.append(
                CoinParticle(
                    pos=QPointF(random.uniform(left, right), random.uniform(-26, -5)),
                    velocity=QPointF(random.uniform(-0.48, 0.48), random.uniform(1.15, 2.05)),
                    radius=random.uniform(5.6, 7.8),
                    life=life,
                    max_life=life,
                    spin=random.uniform(0, 360),
                    gravity=random.uniform(0.008, 0.016),
                    squash=random.uniform(0.68, 0.96),
                    floor_y=random.uniform(floor_y - 7, floor_y + 4),
                )
            )
        self.update()

    def _tick(self) -> None:
        if not self.particles:
            return
        dt = 0.033
        alive = []
        for particle in self.particles:
            particle.life -= dt
            particle.velocity.setY(particle.velocity.y() + particle.gravity)
            particle.pos = QPointF(
                particle.pos.x() + particle.velocity.x(),
                particle.pos.y() + particle.velocity.y(),
            )
            if particle.floor_y is not None and particle.pos.y() > particle.floor_y and particle.velocity.y() > 0:
                particle.pos = QPointF(particle.pos.x(), particle.floor_y)
                particle.velocity = QPointF(particle.velocity.x() * 0.65, -particle.velocity.y() * 0.22)
                particle.squash = min(particle.squash, 0.55)
            particle.spin += 8
            if particle.life > 0 and particle.pos.y() < self.height() + 22:
                alive.append(particle)
        self.particles = alive
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for particle in self.particles:
            alpha = max(0, min(230, int(230 * particle.life / particle.max_life)))
            center = particle.pos
            radius = particle.radius
            width_scale = 0.66 + 0.34 * abs(math.cos(math.radians(particle.spin)))
            coin_rect = QRectF(
                center.x() - radius * width_scale,
                center.y() - radius * particle.squash,
                radius * 2 * width_scale,
                radius * 2 * particle.squash,
            )
            painter.setPen(QPen(QColor(255, 248, 196, alpha), 1.35))
            painter.setBrush(QColor(255, 190, 58, alpha))
            painter.drawEllipse(coin_rect)
            painter.setPen(QPen(QColor(255, 245, 180, int(alpha * 0.78)), 1.1))
            painter.drawLine(
                QPointF(coin_rect.left() + coin_rect.width() * 0.32, coin_rect.top() + 1),
                QPointF(coin_rect.left() + coin_rect.width() * 0.22, coin_rect.bottom() - 1),
            )
            painter.setPen(QPen(QColor(150, 94, 20, int(alpha * 0.55)), 0.9))
            painter.drawEllipse(coin_rect.adjusted(radius * 0.42, radius * 0.46, -radius * 0.42, -radius * 0.46))


class QuoteBubble(QWidget):
    """小组件外部弹出的语录气泡，点击或超时自动隐藏。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.setFixedWidth(250)
        self.label.setStyleSheet(
            """
            QLabel {
                color: #2b2118;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 13px;
                font-weight: 700;
                line-height: 1.35;
                background: transparent;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.addWidget(self.label)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_quote(self, text: str, anchor: QWidget) -> None:
        if not text:
            return
        self.label.setText(text)
        self.adjustSize()
        self._place_near(anchor)
        self.show()
        self.raise_()
        self.hide_timer.start(QUOTE_POPUP_DURATION_MS)

    def _place_near(self, anchor: QWidget) -> None:
        anchor_rect = anchor.frameGeometry()
        screen = QApplication.screenAt(anchor_rect.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

        x = anchor_rect.center().x() - self.width() // 2
        y = anchor_rect.top() - self.height() - 10
        if y < available.top() + 8:
            y = anchor_rect.bottom() + 10

        x = max(available.left() + 8, min(x, available.right() - self.width() - 8))
        y = max(available.top() + 8, min(y, available.bottom() - self.height() - 8))
        self.move(x, y)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.hide()
        event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 4, -5, -5)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(42, 34, 26, 82))
        painter.drawRoundedRect(rect.translated(3, 4), 13, 13)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(255, 250, 225, 236))
        gradient.setColorAt(1.0, QColor(255, 219, 117, 228))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 13, 13)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(43, 34, 26, 190), 2.0))
        painter.drawRoundedRect(rect, 13, 13)


def enable_windows_acrylic(widget: QWidget) -> None:
    """启用 Windows 10/11 Acrylic 毛玻璃。失败时保留半透明绘制作为降级效果。"""
    if sys.platform != "win32":
        return

    try:
        hwnd = int(widget.winId())

        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_int),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
        WCA_ACCENT_POLICY = 19
        accent = ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        # GradientColor 使用 ABGR 格式：alpha + blue + green + red。
        accent.GradientColor = (0x92 << 24) | (0x32 << 16) | (0x25 << 8) | 0x1D
        data = WINDOWCOMPOSITIONATTRIBDATA(
            WCA_ACCENT_POLICY,
            ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
            ctypes.sizeof(accent),
        )
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
    except Exception:
        return


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("薪资计时器设置")
        self.setModal(True)
        self.setMinimumWidth(360)

        self.salary_spin = QDoubleSpinBox(self)
        self.salary_spin.setRange(0, 10_000_000)
        self.salary_spin.setDecimals(2)
        self.salary_spin.setSingleStep(500)
        self.salary_spin.setSuffix(" 元")
        self.salary_spin.setValue(cfg.monthly_salary)

        self.manual_check = QCheckBox("手动覆盖工作日天数", self)
        self.manual_check.setChecked(cfg.manual_workdays)

        self.auto_start_check = QCheckBox("开机自动启动", self)
        self.auto_start_check.setChecked(cfg.auto_start and StartupManager.is_supported())
        self.auto_start_check.setEnabled(StartupManager.is_supported())
        if not StartupManager.is_supported():
            self.auto_start_check.setToolTip("开机自启仅支持 Windows。")

        self.workdays_spin = QSpinBox(self)
        self.workdays_spin.setRange(1, 31)
        self.workdays_spin.setValue(cfg.workdays)

        self.display_combo = QComboBox(self)
        self.display_combo.addItem("今日累计", "day")
        self.display_combo.addItem("本月累计", "month")
        self.display_combo.setCurrentIndex(1 if cfg.display_mode == "month" else 0)

        self.quote_interval_spin = QSpinBox(self)
        self.quote_interval_spin.setRange(1, 240)
        self.quote_interval_spin.setSuffix(" 分钟")
        self.quote_interval_spin.setValue(cfg.quote_interval_minutes)

        self.start_edit = QTimeEdit(self)
        self.start_edit.setDisplayFormat("HH:mm")
        start = parse_hhmm(cfg.work_start, "09:00")
        self.start_edit.setTime(QTime(start.hour, start.minute))

        self.end_edit = QTimeEdit(self)
        self.end_edit.setDisplayFormat("HH:mm")
        end = parse_hhmm(cfg.work_end, "18:00")
        self.end_edit.setTime(QTime(end.hour, end.minute))

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setSingleStep(5)
        self.opacity_slider.setPageStep(10)
        self.opacity_slider.setValue(int(round(cfg.window_opacity * 100)))

        self.opacity_label = QLabel(self)
        self.opacity_label.setFixedWidth(44)
        self.opacity_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opacity_slider.valueChanged.connect(self._sync_opacity_label)
        self._sync_opacity_label(self.opacity_slider.value())

        opacity_widget = QWidget(self)
        opacity_layout = QHBoxLayout(opacity_widget)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        opacity_layout.setSpacing(8)
        opacity_layout.addWidget(self.opacity_slider, 1)
        opacity_layout.addWidget(self.opacity_label)

        self.refresh_button = QPushButton("从 timor API 刷新本月工作日", self)
        self.refresh_button.clicked.connect(self._refresh_workdays_blocking)

        self.status_label = QLabel("自动获取失败时将使用 22 天。", self)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #4c5567;")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("月薪", self.salary_spin)
        form.addRow("", self.manual_check)
        form.addRow("", self.auto_start_check)
        form.addRow("显示模式", self.display_combo)
        form.addRow("语录间隔", self.quote_interval_spin)
        form.addRow("工作日", self.workdays_spin)
        form.addRow("上班时间", self.start_edit)
        form.addRow("下班时间", self.end_edit)
        form.addRow("透明度", opacity_widget)
        form.addRow("", self.refresh_button)
        form.addRow("", self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.setStyleSheet(
            """
            QDialog {
                background: #f8fafc;
                color: #1f2937;
            }
            QDoubleSpinBox, QSpinBox, QTimeEdit, QComboBox {
                min-height: 28px;
                padding: 2px 8px;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background: white;
            }
            QSlider::groove:horizontal {
                height: 6px;
                border-radius: 3px;
                background: #d7dee9;
            }
            QSlider::handle:horizontal {
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
                background: #f59e0b;
            }
            QPushButton {
                min-height: 30px;
                padding: 4px 10px;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background: #ffffff;
            }
            QPushButton:hover {
                background: #eef6ff;
            }
            """
        )

    def _sync_opacity_label(self, value: int) -> None:
        self.opacity_label.setText(f"{value}%")

    def _refresh_workdays_blocking(self) -> None:
        today = date.today()
        self.refresh_button.setEnabled(False)
        self.status_label.setText("正在获取本月工作日...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            days, _ = HolidayService.fetch_month_info(today.year, today.month)
            self.workdays_spin.setValue(days)
            self.manual_check.setChecked(False)
            self.status_label.setText(f"已自动获取 {today.year}-{today.month:02d}：{days} 个工作日。")
        except Exception as exc:
            self.workdays_spin.setValue(DEFAULT_WORKDAYS)
            self.manual_check.setChecked(False)
            self.status_label.setText(f"获取失败，已降级为 {DEFAULT_WORKDAYS} 天：{exc}")
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh_button.setEnabled(True)

    def values(self) -> Dict:
        start = self.start_edit.time()
        end = self.end_edit.time()
        return {
            "monthly_salary": float(self.salary_spin.value()),
            "workdays": int(self.workdays_spin.value()),
            "manual_workdays": self.manual_check.isChecked(),
            "work_start": f"{start.hour():02d}:{start.minute():02d}",
            "work_end": f"{end.hour():02d}:{end.minute():02d}",
            "window_opacity": self.opacity_slider.value() / 100.0,
            "auto_start": self.auto_start_check.isChecked(),
            "display_mode": self.display_combo.currentData(),
            "quote_interval_minutes": int(self.quote_interval_spin.value()),
        }


class SalaryWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.base_dir = app_base_dir()
        self.config_store = ConfigStore(self.base_dir / CONFIG_FILE)
        self.config = self.config_store.load()
        self.thread_pool = QThreadPool.globalInstance()
        self.day_type_map: Dict[str, int] = {}
        self.auto_workdays: Optional[int] = None
        self.workday_source = "默认"
        self.fetching_workdays = False
        self.drag_offset: Optional[QPoint] = None
        self.press_global_pos: Optional[QPoint] = None
        self.dragging_window = False
        self.quitting = False
        self.acrylic_applied = False
        self.quotes: List[str] = self._load_local_quotes()
        self.quote_bubble = QuoteBubble()
        self.quote_shown_this_session = False
        self.next_quote_at = 0.0
        self.guide_is_open = False
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.toggle_display_mode)
        self._sync_startup_from_config()

        self._build_window()
        self._build_tray()
        self._restore_position()
        self._apply_collapsed(self.config.collapsed, persist=False)

        self.refresh_workdays(silent=True)

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_salary)
        self.update_timer.start(1000)
        self.update_salary()
        QTimer.singleShot(650, self._show_usage_guide_once)
        self.refresh_quotes(show_on_success=True)
        QTimer.singleShot(1500, self._show_startup_quote)

        self.quote_timer = QTimer(self)
        self.quote_timer.timeout.connect(self._maybe_show_scheduled_quote)
        self.quote_timer.start(QUOTE_SCHEDULER_TICK_MS)
        self._schedule_next_quote()

        QTimer.singleShot(3500, self.check_for_updates)

    def _show_usage_guide_once(self) -> None:
        if self.config.usage_guide_shown:
            return

        self.config.usage_guide_shown = True
        self.config_store.save(self.config)
        self.guide_is_open = True

        message = QMessageBox(self)
        message.setWindowTitle(f"使用说明 - v{APP_VERSION}")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(f"薪资计时器已启动 v{APP_VERSION}")
        message.setInformativeText(
            "单击小组件：切换今日累计 / 本月累计\n"
            "双击小组件：打开设置\n"
            "按住拖动：移动位置\n"
            "右键菜单：语录、更新、透明度、开机自启、托盘和刷新工作日\n"
            "语录会在启动和每隔半小时弹出，点击或 30 秒后隐藏\n"
            "设置会自动保存到本地 config.json"
        )
        message.setStandardButtons(QMessageBox.StandardButton.Ok)
        message.exec()
        self.guide_is_open = False
        self._show_startup_quote()

    def _show_startup_quote(self) -> None:
        if self.quote_shown_this_session:
            return
        if self.guide_is_open:
            QTimer.singleShot(500, self._show_startup_quote)
            return
        self.show_random_quote(startup=True)

    def refresh_quotes(self, show_on_success: bool) -> None:
        if not self.config.quotes_url:
            return
        task = JsonFetchTask(self.config.quotes_url)
        task.signals.finished.connect(lambda data: self._on_quotes_loaded(data, show_on_success))
        task.signals.failed.connect(lambda message: self._on_quotes_failed(message, show_on_success))
        self.thread_pool.start(task)

    def _on_quotes_loaded(self, data: object, show_on_success: bool) -> None:
        quotes = self._normalize_quotes(data)
        if not quotes:
            return
        self.quotes = quotes
        self._save_quotes_cache(quotes)
        if show_on_success and not self.quote_shown_this_session:
            self._show_startup_quote()

    def _on_quotes_failed(self, message: str, show_on_success: bool) -> None:
        if show_on_success and not self.quote_shown_this_session:
            self._show_startup_quote()

    def _normalize_quotes(self, data: object) -> List[str]:
        if isinstance(data, dict):
            items = data.get("quotes", [])
        else:
            items = data
        if not isinstance(items, list):
            return []
        quotes = []
        seen = set()
        for item in items:
            text = str(item).strip()
            if text and text not in seen:
                quotes.append(text)
                seen.add(text)
        return quotes

    def _load_local_quotes(self) -> List[str]:
        for path in (self.base_dir / QUOTES_CACHE_FILE, self.base_dir / "quotes.json"):
            try:
                if path.exists():
                    with path.open("r", encoding="utf-8-sig") as fp:
                        quotes = self._normalize_quotes(json.load(fp))
                    if quotes:
                        return quotes
            except Exception:
                continue
        return [
            "今日状态：人在工位，心在被窝。",
            "工资按秒到账，快乐按周失踪。",
            "上班只是肉体出勤，灵魂还在请假。",
        ]

    def _save_quotes_cache(self, quotes: List[str]) -> None:
        try:
            with (self.base_dir / QUOTES_CACHE_FILE).open("w", encoding="utf-8") as fp:
                json.dump(quotes, fp, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def show_random_quote(self, startup: bool) -> None:
        if not self.quotes:
            return
        candidates = [quote for quote in self.quotes if quote != self.config.last_quote] or self.quotes
        quote = random.choice(candidates)
        self.config.last_quote = quote
        self.config_store.save(self.config)
        if startup:
            self.quote_shown_this_session = True
        self.quote_bubble.show_quote(quote, self)
        if startup:
            self._schedule_next_quote()

    def _quote_interval_seconds(self) -> int:
        return max(1, int(self.config.quote_interval_minutes)) * 60

    def _schedule_next_quote(self) -> None:
        self.next_quote_at = time.monotonic() + self._quote_interval_seconds()

    def _maybe_show_scheduled_quote(self) -> None:
        if time.monotonic() < self.next_quote_at:
            return
        self.show_random_quote(startup=False)
        self._schedule_next_quote()

    def check_for_updates(self, manual: bool = False) -> None:
        if not self.config.update_url:
            return
        task = JsonFetchTask(self.config.update_url)
        task.signals.finished.connect(lambda data: self._on_update_info_loaded(data, manual))
        task.signals.failed.connect(lambda message: self._on_update_info_failed(message, manual))
        self.thread_pool.start(task)

    def _on_update_info_loaded(self, data: object, manual: bool) -> None:
        if not isinstance(data, dict):
            if manual:
                QMessageBox.information(self, "检查更新", "更新信息格式不正确。")
            return

        remote_version = str(data.get("version", "")).strip().lstrip("v")
        download_url = str(data.get("download_url", "")).strip() or DEFAULT_DOWNLOAD_URL
        notes = str(data.get("notes", "")).strip()

        if remote_version and self._version_tuple(remote_version) > self._version_tuple(APP_VERSION):
            message = QMessageBox(self)
            message.setWindowTitle("发现新版本")
            message.setIcon(QMessageBox.Icon.Information)
            message.setText(f"发现 v{remote_version}，当前版本 v{APP_VERSION}")
            detail = notes or "是否打开 GitHub 下载新版？"
            message.setInformativeText(f"{detail}\n\n下载地址：{download_url}")
            message.setStandardButtons(QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel)
            if message.exec() == QMessageBox.StandardButton.Open:
                webbrowser.open(download_url)
        elif manual:
            QMessageBox.information(self, "检查更新", f"当前已是最新版本 v{APP_VERSION}。")

    def _on_update_info_failed(self, message: str, manual: bool) -> None:
        if manual:
            QMessageBox.warning(self, "检查更新失败", message)

    @staticmethod
    def _version_tuple(value: str) -> Tuple[int, int, int]:
        parts = []
        for part in value.split("."):
            digits = "".join(ch for ch in part if ch.isdigit())
            parts.append(int(digits or 0))
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def _build_window(self) -> None:
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(WINDOW_SIZE)
        self.setWindowOpacity(self.config.window_opacity)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.number_widget = RollingNumberWidget(self)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)

        amount_row = QHBoxLayout()
        amount_row.setSpacing(0)
        amount_row.addWidget(self.number_widget, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 8)
        layout.setSpacing(2)
        layout.addLayout(amount_row)
        layout.addWidget(self.progress)

        self.particle_overlay = CoinParticleOverlay(self)
        self.particle_overlay.raise_()

        for widget in (
            self.number_widget,
            self.progress,
        ):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.setStyleSheet(
            """
            QLabel {
                background: transparent;
                color: #2f261d;
                font-family: "Microsoft YaHei UI", "Segoe UI";
            }
            QProgressBar {
                border: 1px solid #2b2118;
                border-radius: 3px;
                background: rgba(255, 255, 255, 95);
            }
            QProgressBar::chunk {
                border-radius: 2px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4dd6a3,
                    stop:0.55 #ffd84d,
                    stop:1 #ff695a
                );
            }
            """
        )

    def _build_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self._make_icon(), self)
        self.tray_icon.setToolTip(APP_DISPLAY_NAME)
        self.tray_icon.activated.connect(self._tray_activated)

        tray_menu = QMenu()
        show_action = QAction("显示/隐藏", self)
        show_action.triggered.connect(self.toggle_visible)
        tray_menu.addAction(show_action)

        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self.open_settings)
        tray_menu.addAction(settings_action)

        refresh_action = QAction("刷新工作日", self)
        refresh_action.triggered.connect(lambda: self.refresh_workdays(silent=False))
        tray_menu.addAction(refresh_action)

        tray_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    @staticmethod
    def _make_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(43, 34, 26))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(11, 13, 45, 43), 14, 14)
        gradient = QLinearGradient(QPointF(9, 8), QPointF(50, 52))
        gradient.setColorAt(0, QColor(255, 250, 214))
        gradient.setColorAt(0.55, QColor(255, 213, 72))
        gradient.setColorAt(1, QColor(255, 111, 86))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(43, 34, 26), 4))
        painter.drawRoundedRect(QRectF(8, 8, 45, 43), 14, 14)
        painter.setPen(QPen(QColor(255, 250, 214, 190), 4.0))
        painter.drawLine(QPointF(20, 18), QPointF(39, 14))
        painter.setPen(QPen(QColor(142, 83, 18, 120), 2.0))
        painter.drawEllipse(QRectF(27, 28, 8, 6))
        painter.end()
        return QIcon(pixmap)

    def _restore_position(self) -> None:
        if self.config.window_x is not None and self.config.window_y is not None:
            self.move(int(self.config.window_x), int(self.config.window_y))
            return
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.move(available.right() - self.width() - 24, available.top() + 96)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.acrylic_applied:
            self.acrylic_applied = True
            # 漫画皮肤使用自绘面板，避免 Acrylic 在拖动时带来额外重绘压力。
            self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.particle_overlay.setGeometry(self.rect())
        self.particle_overlay.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(5, 5, -6, -6)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(42, 34, 26, 72))
        painter.drawRoundedRect(rect.translated(3, 4), 14, 14)

        painter.setBrush(QColor(255, 246, 205, 186))
        painter.drawRoundedRect(rect, 14, 14)

        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.setClipPath(path)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(255, 249, 220, 178))
        gradient.setColorAt(0.52, QColor(255, 225, 112, 150))
        gradient.setColorAt(1.0, QColor(255, 142, 101, 136))
        painter.fillPath(path, gradient)

        painter.setPen(Qt.PenStyle.NoPen)
        for row in range(0, int(rect.height()) + 16, 16):
            for col in range(0, int(rect.width()) + 16, 16):
                x = rect.left() + col + (8 if row // 16 % 2 else 0)
                y = rect.top() + row
                if x < rect.center().x() + 20 and y > rect.top() + 18:
                    continue
                painter.setBrush(QColor(43, 34, 26, 14))
                painter.drawEllipse(QPointF(x, y), 1.4, 1.4)

        painter.setPen(QPen(QColor(255, 255, 255, 54), 1.2))
        painter.drawLine(QPointF(rect.left() + 14, rect.top() + 15), QPointF(rect.right() - 34, rect.top() + 9))
        painter.setClipping(False)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 108), 1.0))
        painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 11, 11)
        painter.setPen(QPen(QColor(43, 34, 26, 180), 2.2))
        painter.drawRoundedRect(rect, 14, 14)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self.open_settings)
        menu.addAction(settings_action)

        refresh_action = QAction("刷新本月工作日", self)
        refresh_action.triggered.connect(lambda: self.refresh_workdays(silent=False))
        menu.addAction(refresh_action)

        quote_action = QAction("来一句语录", self)
        quote_action.triggered.connect(lambda: self.show_random_quote(startup=False))
        menu.addAction(quote_action)

        update_action = QAction("检查更新", self)
        update_action.triggered.connect(lambda: self.check_for_updates(manual=True))
        menu.addAction(update_action)

        fold_action = QAction("折叠/展开", self)
        fold_action.triggered.connect(self.toggle_collapsed)
        menu.addAction(fold_action)

        startup_action = QAction("开机自启", self)
        startup_action.setCheckable(True)
        startup_action.setChecked(self.config.auto_start and StartupManager.is_enabled())
        startup_action.setEnabled(StartupManager.is_supported())
        startup_action.triggered.connect(self.toggle_auto_start)
        menu.addAction(startup_action)

        opacity_menu = menu.addMenu("透明度")
        for percent in (0, 25, 40, 55, 70, 82, 100):
            action = QAction(f"{percent}%", self)
            action.setCheckable(True)
            action.setChecked(abs(self.config.window_opacity - percent / 100.0) < 0.015)
            action.triggered.connect(lambda checked=False, value=percent / 100.0: self.set_opacity(value))
            opacity_menu.addAction(action)

        tray_action = QAction("最小化到托盘", self)
        tray_action.triggered.connect(self.hide_to_tray)
        menu.addAction(tray_action)

        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.exit_app)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.click_timer.stop()
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.press_global_pos = event.globalPosition().toPoint()
            self.dragging_window = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.raise_()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current_pos = event.globalPosition().toPoint()
            if self.press_global_pos is not None:
                delta = current_pos - self.press_global_pos
                if not self.dragging_window and delta.manhattanLength() >= QApplication.startDragDistance():
                    self.dragging_window = True
            if self.dragging_window:
                self.move(current_pos - self.drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.drag_offset is not None:
            was_dragging = self.dragging_window
            self.drag_offset = None
            self.press_global_pos = None
            self.dragging_window = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if was_dragging:
                self._save_position()
            else:
                app = QApplication.instance()
                interval = app.doubleClickInterval() if app else 250
                self.click_timer.start(interval)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.click_timer.stop()
            self.drag_offset = None
            self.press_global_pos = None
            self.dragging_window = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.open_settings()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def closeEvent(self, event) -> None:
        if self.quitting:
            event.accept()
            return
        event.ignore()
        self.hide_to_tray()

    def refresh_workdays(self, silent: bool) -> None:
        if self.fetching_workdays:
            return
        today = date.today()
        month_key = f"{today.year}-{today.month:02d}"
        self.fetching_workdays = True

        task = WorkdayFetchTask(today.year, today.month)
        task.signals.finished.connect(lambda days, types: self._on_workdays_loaded(days, types, month_key, silent))
        task.signals.failed.connect(lambda message: self._on_workdays_failed(message, month_key, silent))
        self.thread_pool.start(task)

    def _on_workdays_loaded(self, days: int, day_types: Dict[str, int], month_key: str, silent: bool) -> None:
        self.fetching_workdays = False
        self.day_type_map = day_types
        self.auto_workdays = days
        self.workday_source = "自动"
        if not self.config.manual_workdays:
            self.config.workdays = max(1, days)
            self.config.workdays_month = month_key
            self.config_store.save(self.config)
        if not silent:
            self.tray_icon.showMessage(
                APP_DISPLAY_NAME,
                f"已获取 {month_key} 工作日：{days} 天",
                QSystemTrayIcon.MessageIcon.Information,
                1800,
            )
        self.update_salary()

    def _on_workdays_failed(self, message: str, month_key: str, silent: bool) -> None:
        self.fetching_workdays = False
        self.day_type_map = {}
        self.auto_workdays = None
        if not self.config.manual_workdays:
            self.config.workdays = DEFAULT_WORKDAYS
            self.config.workdays_month = month_key
            self.config_store.save(self.config)
            self.workday_source = "默认"
        else:
            self.workday_source = "手动"
        if not silent:
            QMessageBox.warning(self, "工作日获取失败", f"已降级为 {DEFAULT_WORKDAYS} 天。\n\n{message}")
        self.update_salary()

    def update_salary(self) -> None:
        now = datetime.now()
        today_key = f"{now.year}-{now.month:02d}"
        if self.config.workdays_month != today_key and not self.config.manual_workdays:
            self.refresh_workdays(silent=True)

        today_seconds, state = self._today_work_seconds(now)
        month_seconds = self._month_work_seconds(now, today_seconds)
        workdays = max(1, int(self.config.workdays))
        daily_salary = self.config.monthly_salary / workdays
        per_second_salary = daily_salary / SECONDS_PER_PAY_DAY
        display_seconds = month_seconds if self.config.display_mode == "month" else today_seconds
        amount = per_second_salary * display_seconds

        self.number_widget.set_value(amount)
        target_salary = self.config.monthly_salary if self.config.display_mode == "month" else daily_salary
        progress = 0 if target_salary <= 0 else min(1.0, amount / target_salary)
        self.progress.setValue(int(progress * 1000))

        if today_seconds > 0:
            number_top_left = self.number_widget.mapTo(self, QPoint(0, 0))
            number_floor = number_top_left.y() + self.number_widget.height() - 6
            if random.random() < 0.48:
                self.particle_overlay.drop(
                    14,
                    max(34, self.width() - 14),
                    number_floor,
                    random.randint(1, 3),
                )
            elif random.random() < 0.12:
                origin = self.number_widget.mapTo(
                    self,
                    QPoint(random.randint(10, max(11, self.number_widget.width() - 10)), 22),
                )
                self.particle_overlay.sparkle(QPointF(origin), random.randint(1, 2))

    def _month_work_seconds(self, now: datetime, today_seconds: int) -> int:
        seconds = today_seconds
        current = date(now.year, now.month, 1)
        while current < now.date():
            if self._is_workday(current):
                seconds += SECONDS_PER_PAY_DAY
            current += timedelta(days=1)
        return seconds

    def _today_work_seconds(self, now: datetime) -> Tuple[int, str]:
        start_time = parse_hhmm(self.config.work_start, "09:00")
        end_time = parse_hhmm(self.config.work_end, "18:00")
        start_dt, end_dt = self._work_window(now, start_time, end_time)

        if not self._is_workday(start_dt.date()):
            return 0, "休息日"
        if now < start_dt:
            return 0, "未开始"
        if now >= end_dt:
            elapsed = int((end_dt - start_dt).total_seconds())
            return max(0, elapsed), "已收工"
        return max(0, int((now - start_dt).total_seconds())), "计时中"

    @staticmethod
    def _work_window(now: datetime, start_time: dt_time, end_time: dt_time) -> Tuple[datetime, datetime]:
        start_dt = datetime.combine(now.date(), start_time)
        end_dt = datetime.combine(now.date(), end_time)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
            if now < start_dt and now.time() <= end_time:
                start_dt -= timedelta(days=1)
                end_dt -= timedelta(days=1)
        return start_dt, end_dt

    def _is_workday(self, current: date) -> bool:
        kind = self.day_type_map.get(current.isoformat())
        if kind in (0, 3):
            return True
        if kind in (1, 2):
            return False
        return current.weekday() < 5

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.config.monthly_salary = values["monthly_salary"]
        self.config.workdays = values["workdays"]
        self.config.manual_workdays = values["manual_workdays"]
        self.config.work_start = values["work_start"]
        self.config.work_end = values["work_end"]
        self.config.auto_start = values["auto_start"]
        self.config.display_mode = values["display_mode"]
        old_quote_interval = self.config.quote_interval_minutes
        self.config.quote_interval_minutes = values["quote_interval_minutes"]
        self.set_opacity(values["window_opacity"], persist=False)
        self._apply_startup_setting(show_error=True)
        if self.config.manual_workdays:
            self.workday_source = "手动"
        else:
            self.config.workdays_month = ""
            self.refresh_workdays(silent=True)
        self.config_store.save(self.config)
        if self.config.quote_interval_minutes != old_quote_interval:
            self._schedule_next_quote()
        self.update_salary()

    def set_opacity(self, value: float, persist: bool = True) -> None:
        self.config.window_opacity = max(0.0, min(1.0, float(value)))
        self.setWindowOpacity(self.config.window_opacity)
        if persist:
            self.config_store.save(self.config)

    def toggle_display_mode(self) -> None:
        self.config.display_mode = "month" if self.config.display_mode == "day" else "day"
        self.config_store.save(self.config)
        self.update_salary()

    def toggle_auto_start(self, checked: bool) -> None:
        self.config.auto_start = bool(checked)
        self._apply_startup_setting(show_error=True)
        self.config_store.save(self.config)

    def _sync_startup_from_config(self) -> None:
        if self.config.auto_start:
            self._apply_startup_setting(show_error=False)

    def _apply_startup_setting(self, show_error: bool) -> None:
        try:
            StartupManager.set_enabled(self.config.auto_start)
        except Exception as exc:
            self.config.auto_start = StartupManager.is_enabled()
            if show_error:
                QMessageBox.warning(self, "开机自启设置失败", str(exc))

    def toggle_collapsed(self) -> None:
        self._apply_collapsed(not self.config.collapsed, persist=True)

    def _apply_collapsed(self, collapsed: bool, persist: bool) -> None:
        self.config.collapsed = collapsed
        self.progress.setVisible(not collapsed)
        self.setFixedSize(COLLAPSED_SIZE if collapsed else WINDOW_SIZE)
        if persist:
            self.config_store.save(self.config)

    def hide_to_tray(self) -> None:
        self._save_position()
        self.hide()
        if self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                APP_DISPLAY_NAME,
                "已最小化到系统托盘。",
                QSystemTrayIcon.MessageIcon.Information,
                1200,
            )

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_visible()

    def _save_position(self) -> None:
        self.config.window_x = int(self.x())
        self.config.window_y = int(self.y())
        self.config_store.save(self.config)

    def exit_app(self) -> None:
        self.quitting = True
        self._save_position()
        self.tray_icon.hide()
        QApplication.instance().quit()


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)

    widget = SalaryWidget()
    widget.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
