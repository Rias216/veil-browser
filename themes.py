from dataclasses import dataclass
from enum import Enum


class ThemeMode(Enum):
    DARK = "dark"
    LIGHT = "light"


@dataclass
class ThemeColors:
    bg: str
    surface: str
    surface_hover: str
    surface_pressed: str
    text: str
    text_secondary: str
    text_tertiary: str
    text_muted: str
    border: str
    border_hover: str
    border_focus: str
    accent: str
    accent_hover: str
    accent_muted: str
    toolbar_bg: str
    tab_bar_bg: str
    divider: str
    progress_bg: str
    overlay: str
    scrollbar: str
    scrollbar_hover: str
    tab_active: str
    tab_active_border: str
    tab_inactive_bg: str
    tab_hover: str
    indicator: str
    shadow: str
    input_bg: str
    badge_bg: str
    danger: str
    lock_secure: str
    lock_insecure: str
    suggestion_bg: str
    suggestion_hover: str
    suggestion_text: str


VOID_DARK = ThemeColors(
    bg="#0e0e10",
    surface="#18181b",
    surface_hover="#222225",
    surface_pressed="#151518",
    text="#ecedf1",
    text_secondary="#a8a8ae",
    text_tertiary="#8a8a90",
    text_muted="#3a3a3e",
    border="#2d2d32",
    border_hover="#3f3f44",
    border_focus="#4c4c52",
    accent="#ffffff",
    accent_hover="#ecedf1",
    accent_muted="#12ffffff",
    toolbar_bg="#0c0c0f",
    tab_bar_bg="#0c0c0f",
    divider="#26262b",
    progress_bg="#2a2a2e",
    overlay="#bf000000",
    scrollbar="#404046",
    scrollbar_hover="#5a5a60",
    tab_active="#0e0e12",
    tab_active_border="#18ffffff",
    tab_inactive_bg="#121216",
    tab_hover="#1e1e24",
    indicator="#d4d4d9",
    shadow="#99000000",
    input_bg="#0e0e10",
    badge_bg="#2a2a2e",
    danger="#ff453a",
    lock_secure="#78ffa8",
    lock_insecure="#ff9f0a",
    suggestion_bg="#18181b",
    suggestion_hover="#222225",
    suggestion_text="#ecedf1",
)

VOID_LIGHT = ThemeColors(
    bg="#fafafc",
    surface="#ffffff",
    surface_hover="#f2f2f5",
    surface_pressed="#eaeaed",
    text="#111113",
    text_secondary="#6e6e72",
    text_tertiary="#a5a5a9",
    text_muted="#18000000",
    border="#d5d5d9",
    border_hover="#c0c0c4",
    border_focus="#aaaaae",
    accent="#111113",
    accent_hover="#2a2a2e",
    accent_muted="#0f000000",
    toolbar_bg="#e8e9ec",
    tab_bar_bg="#e8e9ec",
    divider="#c0c0c6",
    progress_bg="#dcdce0",
    overlay="#ccffffff",
    scrollbar="#b8b8be",
    scrollbar_hover="#9898a0",
    tab_active="#ffffff",
    tab_active_border="#c8c8ce",
    tab_inactive_bg="#d5d5da",
    tab_hover="#c6c6cc",
    indicator="#111113",
    shadow="#14000000",
    input_bg="#ffffff",
    badge_bg="#d5d5d9",
    danger="#ff3b30",
    lock_secure="#111113",
    lock_insecure="#ff9500",
    suggestion_bg="#eeeff2",
    suggestion_hover="#ffffff",
    suggestion_text="#111113",
)


@dataclass
class Theme:
    name: str
    label: str
    mode: ThemeMode
    colors: ThemeColors
    font_family: str = "'Segoe UI Variable Display','Segoe UI',Inter,-apple-system,BlinkMacSystemFont,Roboto,'Helvetica Neue',sans-serif"
    font_mono: str = "'JetBrains Mono','Fira Code','SF Mono',ui-monospace,monospace"
    font_size: str = "13px"
    radius_xs: int = 4
    radius_sm: int = 6
    radius_md: int = 10
    radius_lg: int = 14
    radius_xl: int = 20
    radius_full: int = 9999
    tab_height: int = 30
    tab_radius: int = 7
    tab_padding: str = "0 10px"
    toolbar_height: int = 38
    toolbar_padding: str = "0 10px"
    progress_visible: bool = True
    progress_height: int = 2
    statusbar_visible: bool = False
    scrollbar_width: int = 5
    scrollbar_thumb_radius: int = 3


THEMES = {
    "void": Theme(
        name="void",
        label="Void",
        mode=ThemeMode.DARK,
        colors=VOID_DARK,
    ),
    "void-light": Theme(
        name="void-light",
        label="Void Light",
        mode=ThemeMode.LIGHT,
        colors=VOID_LIGHT,
    ),
}


DEFAULT_THEME = "void"


def get_theme(name: str) -> Theme:
    return THEMES.get(name, THEMES[DEFAULT_THEME])
