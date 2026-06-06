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
    text="#d4d4d9",
    text_secondary="#7ad4d4d9",
    text_tertiary="#38d4d4d9",
    text_muted="#0affffff",
    border="#0fffffff",
    border_hover="#1affffff",
    border_focus="#24ffffff",
    accent="#d4d4d9",
    accent_hover="#bfd4d4d9",
    accent_muted="#0fffffff",
    toolbar_bg="#0a0a0c",
    tab_bar_bg="#0a0a0c",
    divider="#0affffff",
    progress_bg="#05ffffff",
    overlay="#bf000000",
    scrollbar="#14ffffff",
    scrollbar_hover="#24ffffff",
    tab_active="#0e0e10",
    tab_active_border="#0fffffff",
    tab_inactive_bg="#66ffffff",
    tab_hover="#1fffffff",
    indicator="#d4d4d9",
    shadow="#99000000",
    input_bg="#0e0e10",
    badge_bg="#12ffffff",
    danger="#ff453a",
    lock_secure="#99d4d4d9",
    lock_insecure="#ff9f0a",
    suggestion_bg="#18181b",
    suggestion_hover="#222225",
    suggestion_text="#d9d4d4d9",
)

VOID_LIGHT = ThemeColors(
    bg="#f2f2f5",
    surface="#e6e6e9",
    surface_hover="#dbdbde",
    surface_pressed="#d0d0d3",
    text="#1c1c1e",
    text_secondary="#801c1c1e",
    text_tertiary="#381c1c1e",
    text_muted="#0a000000",
    border="#12000000",
    border_hover="#1f000000",
    border_focus="#2e000000",
    accent="#1c1c1e",
    accent_hover="#cc1c1c1e",
    accent_muted="#0d000000",
    toolbar_bg="#e6e6e9",
    tab_bar_bg="#e6e6e9",
    divider="#0f000000",
    progress_bg="#08000000",
    overlay="#ccffffff",
    scrollbar="#1a000000",
    scrollbar_hover="#2e000000",
    tab_active="#f2f2f5",
    tab_active_border="#14000000",
    tab_inactive_bg="#26000000",
    tab_hover="#1a000000",
    indicator="#1c1c1e",
    shadow="#14000000",
    input_bg="#f2f2f5",
    badge_bg="#0f000000",
    danger="#ff3b30",
    lock_secure="#991c1c1e",
    lock_insecure="#ff9500",
    suggestion_bg="#e6e6e9",
    suggestion_hover="#dbdbde",
    suggestion_text="#d91c1c1e",
)


@dataclass
class Theme:
    name: str
    label: str
    mode: ThemeMode
    colors: ThemeColors
    font_family: str = "'Segoe UI', SF Pro Display, -apple-system, BlinkMacSystemFont, Inter, system-ui, sans-serif"
    font_mono: str = "'SF Mono', 'JetBrains Mono', 'Fira Code', ui-monospace, monospace"
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
