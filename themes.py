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
    divider: str
    progress_bg: str
    overlay: str
    scrollbar: str
    scrollbar_hover: str
    tab_active: str
    tab_hover: str
    indicator: str
    shadow: str
    input_bg: str
    badge_bg: str
    danger: str


VOID_DARK = ThemeColors(
    bg="#000000",
    surface="#0a0a0a",
    surface_hover="#141414",
    surface_pressed="#080808",
    text="#ffffff",
    text_secondary="rgba(255,255,255,0.55)",
    text_tertiary="rgba(255,255,255,0.28)",
    text_muted="rgba(255,255,255,0.10)",
    border="rgba(255,255,255,0.06)",
    border_hover="rgba(255,255,255,0.12)",
    border_focus="rgba(255,255,255,0.18)",
    accent="#ffffff",
    accent_hover="rgba(255,255,255,0.85)",
    accent_muted="rgba(255,255,255,0.08)",
    toolbar_bg="#000000",
    divider="rgba(255,255,255,0.05)",
    progress_bg="rgba(255,255,255,0.04)",
    overlay="rgba(0,0,0,0.70)",
    scrollbar="rgba(255,255,255,0.12)",
    scrollbar_hover="rgba(255,255,255,0.22)",
    tab_active="#0a0a0a",
    tab_hover="#060606",
    indicator="#ffffff",
    shadow="rgba(0,0,0,0.50)",
    input_bg="#0a0a0a",
    badge_bg="rgba(255,255,255,0.10)",
    danger="#ff453a",
)

VOID_LIGHT = ThemeColors(
    bg="#ffffff",
    surface="#f5f5f5",
    surface_hover="#ececec",
    surface_pressed="#e0e0e0",
    text="#000000",
    text_secondary="rgba(0,0,0,0.50)",
    text_tertiary="rgba(0,0,0,0.25)",
    text_muted="rgba(0,0,0,0.08)",
    border="rgba(0,0,0,0.06)",
    border_hover="rgba(0,0,0,0.12)",
    border_focus="rgba(0,0,0,0.18)",
    accent="#000000",
    accent_hover="rgba(0,0,0,0.80)",
    accent_muted="rgba(0,0,0,0.05)",
    toolbar_bg="#ffffff",
    divider="rgba(0,0,0,0.06)",
    progress_bg="rgba(0,0,0,0.04)",
    overlay="rgba(255,255,255,0.80)",
    scrollbar="rgba(0,0,0,0.12)",
    scrollbar_hover="rgba(0,0,0,0.22)",
    tab_active="#f5f5f5",
    tab_hover="#f0f0f0",
    indicator="#000000",
    shadow="rgba(0,0,0,0.06)",
    input_bg="#f5f5f5",
    badge_bg="rgba(0,0,0,0.06)",
    danger="#ff3b30",
)


@dataclass
class Theme:
    name: str
    label: str
    mode: ThemeMode
    colors: ThemeColors
    font_family: str = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    font_mono: str = "'SF Mono', 'JetBrains Mono', 'Fira Code', ui-monospace, monospace"
    font_size: str = "13px"
    radius_xs: int = 4
    radius_sm: int = 6
    radius_md: int = 10
    radius_lg: int = 14
    radius_xl: int = 20
    radius_full: int = 9999
    tab_height: int = 34
    tab_padding: str = "0 14px"
    toolbar_height: int = 44
    toolbar_padding: str = "0 12px"
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
