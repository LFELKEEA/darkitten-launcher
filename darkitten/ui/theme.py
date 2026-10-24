from __future__ import annotations

LIGHT = {
    "bg": "#F7EFF2",
    "card": "#FFFFFF",
    "card2": "#FDF6F8",
    "border": "#EEDDE3",
    "border_strong": "#E2C9D2",
    "text": "#2A1A21",
    "text2": "#6E5560",
    "muted": "#9C818C",
    "accent": "#D62B50",
    "accent_hover": "#BE1F43",
    "accent_soft": "#FBE8EE",
    "accent_border": "#F3C5D1",
    "hover": "#F9EEF2",
    "input": "#FDF6F8",
    "green": "#178A50",
    "green_soft": "#E4F6EC",
    "orange": "#B06A12",
    "orange_soft": "#FFF0DC",
    "purple": "#7B4FD1",
    "purple_soft": "#F1EAFF",
    "red": "#C92F45",
    "red_soft": "#FCE8EB",
    "hero_a": "#2A0D18",
    "hero_b": "#4B1226",
    "hero_c": "#1D0E24",
    "log_bg": "#170F14",
    "log_text": "#DCC7CF",
    "log_border": "#2E2028",
    "shadow": "rgba(70, 20, 40, 40)",
    "toast": "#2B1A23",
    "toast_text": "#FFFFFF",
}

DARK = {
    "bg": "#120E15",
    "card": "#1B141B",
    "card2": "#241A23",
    "border": "#352631",
    "border_strong": "#4A3342",
    "text": "#F5E9EE",
    "text2": "#CDB4BF",
    "muted": "#A48A96",
    "accent": "#E23A5E",
    "accent_hover": "#F04D70",
    "accent_soft": "#3A1826",
    "accent_border": "#5E2438",
    "hover": "#2A1D29",
    "input": "#221820",
    "green": "#7FE0A8",
    "green_soft": "#17301F",
    "orange": "#F2B35C",
    "orange_soft": "#33240F",
    "purple": "#C79BFF",
    "purple_soft": "#2C1E3E",
    "red": "#FF6B81",
    "red_soft": "#3A1620",
    "hero_a": "#210F1C",
    "hero_b": "#3D1226",
    "hero_c": "#1A0E22",
    "log_bg": "#0D0A0F",
    "log_text": "#D9C4CD",
    "log_border": "#2E2028",
    "shadow": "rgba(0, 0, 0, 90)",
    "toast": "#2B1A23",
    "toast_text": "#FFFFFF",
}

_current = "light"
_current_colors = LIGHT


def colors(theme: str | None = None) -> dict[str, str]:
    if theme is None:
        return _current_colors
    return DARK if theme == "dark" else LIGHT


def set_current(theme: str) -> None:
    global _current, _current_colors
    _current = "dark" if theme == "dark" else "light"
    _current_colors = DARK if _current == "dark" else LIGHT


def stylesheet(theme: str) -> str:
    c = colors(theme)
    set_current(theme)
    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
    outline: none;
}}
QWidget {{
    color: {c['text']};
}}
#Root {{
    background: {c['bg']};
    border: 1px solid {c['border_strong']};
}}
#TitleBar {{
    background: {c['card']};
    border-bottom: 1px solid {c['border']};
}}
#AppName {{
    font-size: 13px;
    font-weight: 800;
    color: {c['accent']};
}}
#AppTag {{
    font-size: 9px;
    font-weight: 600;
    color: {c['muted']};
    letter-spacing: 2px;
}}
#PageTitle {{
    font-size: 22px;
    font-weight: 700;
    color: {c['text']};
}}
#H1 {{
    font-size: 26px;
    font-weight: 800;
    color: {c['text']};
}}
#H2 {{
    font-size: 15px;
    font-weight: 700;
    color: {c['text']};
}}
#Muted {{
    color: {c['text2']};
}}
#Tiny {{
    color: {c['muted']};
    font-size: 11px;
}}
#HeroTitle {{
    color: #FFFFFF;
    font-size: 27px;
    font-weight: 800;
}}
#HeroSub {{
    color: rgba(255,255,255,194);
    font-size: 13px;
}}
#HeroPill {{
    color: #EAF7F0;
    background: rgba(255,255,255,33);
    border: 1px solid rgba(255,255,255,36);
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
}}
#Hero {{
    border-radius: 16px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {c['hero_a']}, stop:0.52 {c['hero_b']}, stop:1 {c['hero_c']});
}}
#Card {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 13px;
}}
#SoftCard {{
    background: {c['card2']};
    border: 1px solid {c['border']};
    border-radius: 11px;
}}
#FeedbackRow {{
    background: {c['card2']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}
#FeedbackRow:hover {{
    background: {c['card']};
    border: 1px solid {c['accent']};
}}
#Divider {{
    background: {c['border']};
    min-height: 1px;
    max-height: 1px;
    border: none;
}}
QPushButton {{
    font-weight: 600;
}}
QPushButton#Primary {{
    background: {c['accent']};
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
}}
QPushButton#Primary:hover {{
    background: {c['accent_hover']};
}}
QPushButton#Primary:pressed {{
    background: {c['accent']};
}}
QPushButton#Primary:disabled {{
    background: {c['border_strong']};
    color: {c['muted']};
}}
QPushButton#Secondary {{
    background: transparent;
    color: {c['text2']};
    border: 1px solid {c['border_strong']};
    border-radius: 8px;
    padding: 8px 15px;
}}
QPushButton#Secondary:hover {{
    background: {c['hover']};
    color: {c['text']};
    border-color: {c['accent_border']};
}}
QPushButton#Soft {{
    background: {c['accent_soft']};
    color: {c['accent']};
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
}}
QPushButton#Soft:hover {{
    background: {c['accent_border']};
}}
QPushButton#Ghost {{
    background: transparent;
    color: {c['text2']};
    border: none;
    border-radius: 8px;
    padding: 7px 10px;
}}
QPushButton#Ghost:hover {{
    background: {c['hover']};
    color: {c['text']};
}}
QPushButton#Danger {{
    background: {c['red_soft']};
    color: {c['red']};
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
}}
QPushButton#Danger:hover {{
    background: {c['red']};
    color: #FFFFFF;
}}
QPushButton#Secondary:disabled {{
    background: transparent;
    color: {c['muted']};
    border-color: {c['border']};
}}
QPushButton#Soft:disabled {{
    background: {c['card2']};
    color: {c['muted']};
}}
QPushButton#Ghost:disabled {{
    background: transparent;
    color: {c['muted']};
}}
QPushButton#Danger:disabled {{
    background: {c['card2']};
    color: {c['muted']};
}}
QPushButton#Nav {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 0 14px;
    color: {c['text2']};
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#Nav:hover {{
    background: {c['hover']};
    color: {c['text']};
}}
QPushButton#WinBtn, QPushButton#CloseBtn {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0;
}}
QPushButton#WinBtn:hover {{
    background: {c['hover']};
}}
QPushButton#CloseBtn:hover {{
    background: #E5484D;
}}
QPushButton#IconBtn {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 0;
}}
QPushButton#IconBtn:hover {{
    background: {c['hover']};
}}
QPushButton#Launch {{
    background: #FFFFFF;
    color: #3A0F1C;
    border: none;
    border-radius: 10px;
    padding: 0 22px;
    font-size: 14px;
    font-weight: 800;
}}
QPushButton#Launch:hover {{
    background: #FFE3EA;
}}
QPushButton#Launch:disabled {{
    background: rgba(255,255,255,140);
    color: rgba(58,15,28,140);
}}
QLineEdit, QComboBox, QSpinBox {{
    background: {c['input']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {c['accent']};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {c['accent']};
    background: {c['card']};
}}
QLineEdit:disabled {{
    color: {c['muted']};
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox QAbstractItemView {{
    background: {c['card']};
    color: {c['text']};
    border: 1px solid {c['border_strong']};
    border-radius: 8px;
    padding: 5px;
    selection-background-color: {c['accent_soft']};
    selection-color: {c['accent']};
    outline: none;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {c['border_strong']};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['muted']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {c['border_strong']};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QProgressBar {{
    background: {c['input']};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {c['accent']};
    border-radius: 4px;
}}
QSlider::groove:horizontal {{
    height: 5px;
    background: {c['border']};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {c['accent']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #FFFFFF;
    border: 2px solid {c['accent']};
    width: 15px;
    height: 15px;
    margin: -6px 0;
    border-radius: 9px;
}}
QPlainTextEdit, QTextEdit {{
    background: {c['log_bg']};
    color: {c['log_text']};
    border: 1px solid {c['log_border']};
    border-radius: 9px;
    padding: 8px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
    selection-background-color: #2F4A7A;
}}
QPlainTextEdit#SoftLog {{
    background: {c['input']};
    color: {c['text2']};
    border: 1px solid {c['border']};
}}
QLabel[badge="green"], QLabel[badge="release"] {{
    background: {c['green_soft']};
    color: {c['green']};
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel[badge="orange"], QLabel[badge="snapshot"] {{
    background: {c['orange_soft']};
    color: {c['orange']};
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel[badge="purple"], QLabel[badge="loader"] {{
    background: {c['purple_soft']};
    color: {c['purple']};
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel[badge="blue"] {{
    background: {c['accent_soft']};
    color: {c['accent']};
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel[badge="red"] {{
    background: {c['red_soft']};
    color: {c['red']};
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel[badge="gray"] {{
    background: {c['hover']};
    color: {c['text2']};
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 700;
}}
QFrame#SelectRow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
}}
QFrame#SelectRow:hover {{
    background: {c['hover']};
}}
QFrame#SelectRow[selected="true"] {{
    background: {c['accent_soft']};
    border: 1px solid {c['accent_border']};
}}
QFrame#SegmentBox {{
    background: {c['input']};
    border: 1px solid {c['border']};
    border-radius: 9px;
}}
QPushButton#Segment {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 5px 12px;
    color: {c['text2']};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#Segment:hover {{
    color: {c['text']};
}}
QPushButton#Segment:checked {{
    background: {c['card']};
    color: {c['text']};
    border: 1px solid {c['border']};
}}
#StatusBar {{
    background: {c['card']};
    border-top: 1px solid {c['border']};
}}
#StatusText {{
    color: {c['text2']};
    font-size: 11px;
}}
#StatusStrong {{
    color: {c['text']};
    font-size: 11px;
    font-weight: 600;
}}
#LogPanel {{
    background: {c['card']};
    border-top: 1px solid {c['border']};
}}
#AccountChip {{
    background: {c['card2']};
    border: 1px solid {c['border']};
    border-radius: 9px;
}}
#AccountChip:hover {{
    border-color: {c['accent_border']};
    background: {c['accent_soft']};
}}
#DownloadChip {{
    background: {c['card2']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}
#DownloadChip:hover {{
    border-color: {c['accent_border']};
    background: {c['accent_soft']};
}}
#DownloadChip[state="success"] {{
    border-color: {c['green']};
}}
#DownloadChip[state="error"], #DownloadChip[state="cancelled"] {{
    border-color: {c['orange']};
}}
QProgressBar#ChipProgress {{
    background: {c['border']};
    border: none;
    border-radius: 2px;
    height: 3px;
}}
QProgressBar#ChipProgress::chunk {{
    background: {c['accent']};
    border-radius: 2px;
}}
#DialogCard {{
    background: {c['card']};
    border: 1px solid {c['border_strong']};
    border-radius: 15px;
}}
#DialogTitle {{
    font-size: 17px;
    font-weight: 700;
    color: {c['text']};
}}
#DialogText {{
    color: {c['text2']};
    font-size: 13px;
}}
#Toast {{
    background: {c['toast']};
    color: {c['toast_text']};
    border-radius: 10px;
    padding: 10px 16px;
    font-size: 12px;
    font-weight: 600;
}}
#SegLabel {{
    color: {c['muted']};
    font-size: 11px;
    font-weight: 600;
}}
QToolTip {{
    background: {c['toast']};
    color: {c['toast_text']};
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11px;
}}
QCheckBox {{
    spacing: 7px;
    color: {c['text2']};
}}
QMenu {{
    background: {c['card']};
    color: {c['text']};
    border: 1px solid {c['border_strong']};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {c['accent_soft']};
    color: {c['accent']};
}}
"""
