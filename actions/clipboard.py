"""Clipboard access through GDK, not through an external tool.

StreamController is already a GTK application, so the display's clipboard is
right there; shelling out to xclip or wl-copy would add a dependency that
behaves differently on X11 and Wayland. Clipboard writes must happen on the GTK
main loop, and these callbacks can arrive on a worker thread, so the write is
always marshalled.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib

from ..spotify.format import FORMAT_CUSTOM, format_song
from ..spotify.log import log


def copy_text(text: str) -> bool:
    """Put `text` on the clipboard. Returns False if there is no display."""
    if not text:
        return False

    display = Gdk.Display.get_default()
    if display is None:
        log.warning("Spotify: no display available, cannot copy to the clipboard")
        return False

    def write() -> bool:
        try:
            display.get_clipboard().set(text)
        except Exception:  # noqa: BLE001 - clipboard backends vary by session
            log.exception("Spotify: could not write to the clipboard")
        return False  # do not repeat

    GLib.idle_add(write)
    return True


def copy_current_song(action) -> None:
    """Copy the current track using the action's configured format."""
    track = action.manager.get_playback_state().track
    if track is None:
        action.report_failure()
        return

    text = format_song(
        action.combo_value("format", "track_artist"),
        track,
        custom_template=action.setting("custom_template", "") if action.combo_value("format", "") == FORMAT_CUSTOM else "",
    )

    if text and copy_text(text):
        action.flash("COPIED")
    else:
        action.report_failure()
