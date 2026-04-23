"""System tray icon for SmartUPS.

Uses `pystray` + `Pillow`. Works on Linux (AppIndicator / StatusNotifier), macOS,
and Windows. On KDE Plasma / GNOME the icon appears in the system tray and
reflects the current battery percentage and charging state.

This module is import-safe on systems without `pystray` installed: calling
``TrayIcon.available()`` returns False and the main loop falls back to
terminal-only mode.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont

    _PIL_OK = True
except Exception:  # noqa: BLE001
    Image = None  # type: ignore
    _PIL_OK = False

try:  # pragma: no cover — tested manually on target hardware
    import pystray

    _PYSTRAY_OK = True
except Exception:  # noqa: BLE001
    pystray = None  # type: ignore
    _PYSTRAY_OK = False


def _color_for(percent: float, charging: bool) -> tuple[int, int, int]:
    """Pick an icon background color by battery state."""
    if charging:
        return (30, 140, 255)  # blue — charging
    if percent <= 20:
        return (220, 40, 40)  # red — critical
    if percent <= 50:
        return (230, 180, 20)  # amber — low
    return (40, 180, 60)  # green — healthy


def _render_icon(percent: float, charging: bool):
    """Render a 64x64 icon with battery percentage text."""
    if not _PIL_OK:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(4, 10), (60, 54)], radius=6, fill=_color_for(percent, charging))
    # Battery cap
    draw.rectangle([(26, 4), (38, 10)], fill=_color_for(percent, charging))
    text = f"{int(round(percent))}"
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    # Center text
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((64 - tw) / 2 - bbox[0], (64 - th) / 2 - bbox[1] + 4),
        text,
        fill=(255, 255, 255),
        font=font,
    )
    return img


class TrayIcon:
    """Lightweight system-tray indicator for SmartUPS.

    The icon runs on its own thread so pystray's blocking `run()` does not
    interfere with the sampling loop. The main loop calls ``update()`` after
    each sample to refresh state.
    """

    def __init__(self, on_quit: Optional[Callable[[], None]] = None) -> None:
        self._on_quit = on_quit or (lambda: None)
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._latest_title = "SmartUPS starting…"

    @staticmethod
    def available() -> bool:
        return _PYSTRAY_OK and _PIL_OK

    def start(self, initial_percent: float = 100.0, initial_charging: bool = True) -> None:
        if not _PYSTRAY_OK:
            log.warning("pystray/Pillow not installed — skipping tray icon.")
            return

        def _on_quit_menu(icon, _item):  # noqa: ANN001
            icon.stop()
            self._on_quit()

        self._icon = pystray.Icon(
            "smartups",
            _render_icon(initial_percent, initial_charging),
            "SmartUPS",
            menu=pystray.Menu(
                pystray.MenuItem("Quit SmartUPS", _on_quit_menu),
            ),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def update(self, percent: float, charging: bool, tooltip: str) -> None:
        if self._icon is None:
            return
        self._latest_title = tooltip
        try:
            self._icon.icon = _render_icon(percent, charging)
            self._icon.title = tooltip
        except Exception as e:  # noqa: BLE001
            log.debug("tray update failed: %s", e)

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
