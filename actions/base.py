"""Shared behaviour for every Spotify action.

Handles the parts that are identical everywhere: settings defaults and
migration, device targeting and its UI, the StreamController lifecycle,
listener registration, marquee bookkeeping, and redrawing only when the drawn
result would actually differ.

Subclasses supply their inputs, their own settings rows and their renderer.
None of them performs a Spotify request directly — every event handler hands
work to the manager and returns.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from GtkHelper.ComboRow import SimpleComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionCore import ActionCore

from ..rendering import status as status_render
from ..rendering import theme
from ..rendering.common import size_for
from ..rendering.key import render_glyph_key
from ..spotify.log import log
from ..spotify.manager import DEFAULT_TOPICS
from ..spotify.state import ActionStatus

SCHEMA_VERSION = 1

DEVICE_MODE_ACTIVE = "active"
DEVICE_MODE_SPECIFIC = "specific"


class SpotifyActionBase(ActionCore):
    #: Small caption shown under the glyph on status cards.
    TITLE = "Spotify"

    #: Glyph used by the default rendering and as the fallback status icon.
    ICON = "unknown"

    #: Extra settings keys and their defaults, merged with the shared ones.
    EXTRA_DEFAULTS: dict = {}

    #: Manager topics this action needs to hear about.
    TOPICS = DEFAULT_TOPICS

    #: Whether this action sends player commands and so needs a device target.
    USES_DEVICE_TARGET = True

    #: Redraw about once a second, for anything showing elapsed progress.
    WANTS_TICK = False

    #: Participate in the shared marquee for long titles.
    WANTS_MARQUEE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True

        self._listener_registered = False
        self._marquee_registered = False
        self._last_signature = None
        self._device_group: Adw.PreferencesGroup | None = None
        self._device_rows: list = []

        # Settings are not written here: StreamController only registers an
        # action on its page after every action has been constructed, so
        # set_settings() during __init__ raises. Reads merge defaults instead,
        # and the migrating write happens in on_ready().
        if self.USES_DEVICE_TARGET:
            self._build_device_ui()
        self.build_action_ui()
        self.register_events()

    # -- plumbing ---------------------------------------------------------

    @property
    def manager(self):
        return self.plugin_base.spotify

    @property
    def marquee(self):
        return self.plugin_base.marquee

    @property
    def is_dial(self) -> bool:
        return isinstance(self.input_ident, Input.Dial)

    @property
    def marquee_key(self) -> str:
        return f"{self.action_id}:{self.input_ident}:{self.state}:{id(self)}"

    def image_size(self) -> tuple[int, int]:
        """The real pixel size of this input, falling back to a sane default."""
        try:
            controller_input = self.get_input()
            if controller_input is not None:
                size = controller_input.get_image_size()
                if size and size[0] and size[1]:
                    return (int(size[0]), int(size[1]))
        except Exception:  # noqa: BLE001 - sizing must never break a redraw
            pass
        return size_for(self.is_dial)

    # -- settings ---------------------------------------------------------

    def _defaults(self) -> dict:
        defaults = {"schema_version": SCHEMA_VERSION}
        if self.USES_DEVICE_TARGET:
            defaults["device"] = {"mode": DEVICE_MODE_ACTIVE, "device_id": None}
        defaults.update(self.EXTRA_DEFAULTS)
        return defaults

    def settings(self) -> dict:
        """Stored settings with defaults filled in, and migrated if needed."""
        merged = self._defaults()
        stored = migrate_settings(self.get_settings() or {})
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return merged

    def setting(self, key: str, fallback=None):
        return self.settings().get(key, fallback)

    def combo_value(self, key: str, fallback: str) -> str:
        """Read a ComboRow-backed setting, tolerating either storage shape."""
        value = self.setting(key, fallback)
        if isinstance(value, SimpleComboRowItem):
            return value.get_value()
        if value is None:
            return fallback
        return str(value)

    def int_setting(self, key: str, fallback: int) -> int:
        try:
            return int(round(float(self.setting(key, fallback))))
        except (TypeError, ValueError):
            return fallback

    def _persist_settings(self) -> None:
        """Write the merged settings back once, so the page file is explicit.

        Behaviour never depends on this succeeding — settings() already merges
        defaults — so a failure is logged and ignored.
        """
        stored = self.get_settings() or {}
        merged = self.settings()
        if merged == stored:
            return
        try:
            self.set_settings(merged)
        except Exception:  # noqa: BLE001
            log.exception(f"Spotify: could not persist settings for {self.action_id}")

    # -- device targeting -------------------------------------------------

    def device_target(self) -> tuple[str | None, bool]:
        """(device_id, is_missing) for the configured target."""
        if not self.USES_DEVICE_TARGET:
            return (None, False)
        device = self.settings().get("device") or {}
        mode = device.get("mode", DEVICE_MODE_ACTIVE)
        if isinstance(mode, SimpleComboRowItem):
            mode = mode.get_value()
        return self.manager.resolve_device_id(mode, device.get("device_id"))

    @property
    def device_id(self) -> str | None:
        device_id, missing = self.device_target()
        return None if missing else device_id

    # -- lifecycle --------------------------------------------------------

    def on_ready(self) -> None:
        self._persist_settings()

        if not self._listener_registered:
            self.manager.add_listener(self._on_state_changed, self.TOPICS)
            self._listener_registered = True

        if self.WANTS_MARQUEE and self.marquee is not None and not self._marquee_registered:
            self.marquee.register(self.marquee_key, self._on_marquee_tick)
            self._marquee_registered = True

        self.on_action_ready()

        # Force a redraw: returning to a cached page leaves the internal state
        # untouched but the key image itself was reset.
        self._last_signature = None
        self.render()
        self.manager.refresh_now()

    def on_action_ready(self) -> None:
        """Subclass hook for work that needs the action to be live."""

    def on_update(self) -> None:
        self._last_signature = None
        self.render()

    def on_tick(self) -> None:
        if self.WANTS_TICK:
            self.render()

    def on_remove(self) -> None:
        self._teardown()

    def on_removed_from_cache(self) -> None:
        self._teardown()

    def _teardown(self) -> None:
        """Drop every reference held on this action's behalf.

        Called on both removal paths so page changes cannot leak listeners or
        leave the marquee animating a key that no longer exists.
        """
        if self._listener_registered:
            self.manager.remove_listener(self._on_state_changed)
            self._listener_registered = False
        if self._marquee_registered and self.marquee is not None:
            self.marquee.unregister(self.marquee_key)
            self._marquee_registered = False

    # -- rendering --------------------------------------------------------

    def _on_state_changed(self) -> None:
        self.render()

    def _on_marquee_tick(self) -> None:
        # The scroll offset changed, so the cached signature is stale by design.
        self._last_signature = None
        self.render()

    def render(self) -> None:
        if not self.on_ready_called:
            return

        try:
            if not self.get_is_present() or self.get_state() is None:
                return
            # Another action owns this input's image; keep the state, skip the
            # work, and redraw when ownership comes back via on_update().
            if not self.has_image_control():
                return

            signature = self.state_signature()
            if signature is not None and signature == self._last_signature:
                return
            self._last_signature = signature

            image = self.render_image()
            if image is not None:
                self.set_media(image=image)
        except Warning:
            # set_media raises this when the action is not ready yet.
            pass
        except Exception:  # noqa: BLE001
            log.exception(f"Spotify: failed to render {self.action_id}")
            # A stuck overlay fails every redraw from here on, so clear it and
            # let the next tick draw the key properly.
            self._force_hide_overlay()

    def render_image(self):
        """Subclass hook: the image for the current state."""
        status = self.blocking_status()
        if status is not None:
            return self.render_status(status)
        return render_glyph_key(self.image_size(), self.ICON, color=theme.SPOTIFY_GREEN)

    def state_signature(self):
        """Subclass hook: what makes the drawn image different.

        Returning None disables skipping, which is what animated actions want.
        """
        return None

    def render_status(self, status: ActionStatus, detail: str | None = None):
        return status_render.render_status(
            status,
            is_dial=self.is_dial,
            size=self.image_size(),
            title=self.TITLE,
            detail=detail,
        )

    def blocking_status(self) -> ActionStatus | None:
        """The condition preventing normal operation, if there is one."""
        status = self.manager.get_status()

        if status is ActionStatus.AUTH_REQUIRED:
            return status
        if status in (ActionStatus.RATE_LIMITED, ActionStatus.OFFLINE, ActionStatus.API_ERROR):
            return status

        if self.USES_DEVICE_TARGET:
            _, missing = self.device_target()
            if missing:
                return ActionStatus.NO_DEVICE
            if status in (ActionStatus.NO_DEVICE, ActionStatus.UNAVAILABLE):
                return status

        return None

    def flash(self, text: str, duration: float = 1.2) -> None:
        """Brief confirmation overlay for an action that succeeded."""
        try:
            if not self.get_is_present() or self.get_is_multi_action():
                return
            overlay = status_render.render_success_overlay(self.image_size(), text)
            if overlay.mode != "RGBA":
                overlay = overlay.convert("RGBA")
            self.show_overlay(overlay, duration=duration)
        except Warning:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Spotify: could not show the confirmation overlay")
            # An overlay that failed while being shown is left in place with no
            # timer to remove it, and every later redraw of the key fails on it.
            self._force_hide_overlay()

    def _force_hide_overlay(self) -> None:
        try:
            self.hide_overlay()
        except Exception:  # noqa: BLE001
            pass

    def report_failure(self, duration: float = 1.5) -> None:
        try:
            self.show_error(duration=duration)
        except Warning:
            pass
        except Exception:  # noqa: BLE001
            pass

    # -- marquee ----------------------------------------------------------

    def marquee_enabled(self) -> bool:
        if self.marquee is None or not self.WANTS_MARQUEE:
            return False
        if not self.marquee.enabled:
            return False
        return bool(self.setting("marquee", True))

    def marquee_offset(self) -> int:
        if not self.marquee_enabled():
            return 0
        return self.marquee.offset(self.marquee_key)

    def update_marquee(self, overflow: float, text: str) -> None:
        if self.marquee is None or not self._marquee_registered:
            return
        self.marquee.set_overflow(self.marquee_key, overflow if self.marquee_enabled() else 0.0, text)

    # -- events -----------------------------------------------------------

    def register_events(self) -> None:
        """Subclass hook: add EventAssigners."""

    # -- settings UI ------------------------------------------------------

    def build_action_ui(self) -> None:
        """Subclass hook: create this action's Generative UI rows."""

    def _build_device_ui(self) -> None:
        self._device_mode_row = ComboRow(
            action_core=self,
            var_name="device.mode",
            complex_var_name=True,
            default_value=DEVICE_MODE_ACTIVE,
            items=[
                SimpleComboRowItem(DEVICE_MODE_ACTIVE, "Active Spotify device"),
                SimpleComboRowItem(DEVICE_MODE_SPECIFIC, "A specific device"),
            ],
            title="Target",
            subtitle="Which Spotify device this action controls",
            on_change=self._on_device_mode_changed,
        )

    def _on_device_mode_changed(self, _widget, _new_value, _old_value) -> None:
        self._refresh_device_rows()
        self._last_signature = None
        self.render()

    def get_config_rows(self) -> list:
        rows = []
        if self.USES_DEVICE_TARGET:
            # Rebuilt each time the configurator opens so a device switched on
            # since the last visit appears without restarting StreamController.
            self._device_group = Adw.PreferencesGroup(
                title="Spotify device",
                description="Fixed devices are remembered by ID; Spotify does not guarantee those forever, "
                "so re-pick the device if it stops being found.",
            )
            self._refresh_device_rows()
            rows.append(self._device_group)

        rows.extend(self.get_extra_config_rows())
        return rows

    def get_extra_config_rows(self) -> list:
        """Subclass hook: hand-built rows, in addition to the generated ones."""
        return []

    def _refresh_device_rows(self) -> None:
        group = self._device_group
        if group is None:
            return

        for row in self._device_rows:
            group.remove(row)
        self._device_rows = []

        settings = self.settings().get("device") or {}
        mode = settings.get("mode", DEVICE_MODE_ACTIVE)
        if isinstance(mode, SimpleComboRowItem):
            mode = mode.get_value()
        selected_id = settings.get("device_id")

        if mode != DEVICE_MODE_SPECIFIC:
            row = Adw.ActionRow(
                title="Following the active device",
                subtitle="Commands go wherever Spotify is currently playing.",
            )
            group.add(row)
            self._device_rows.append(row)
            self._add_refresh_row(group)
            return

        devices = self.manager.get_devices()
        if not devices:
            row = Adw.ActionRow(
                title="No Spotify devices found",
                subtitle="Start playing on a device, then refresh."
                if self.manager.is_authenticated
                else "Connect your Spotify account in the plugin settings first.",
            )
            group.add(row)
            self._device_rows.append(row)
            self._add_refresh_row(group)
            return

        for device in devices:
            row = Adw.ActionRow(
                title=device.name,
                subtitle=_device_subtitle(device),
            )
            button = Gtk.CheckButton(active=device.id == selected_id, valign=Gtk.Align.CENTER)
            button.connect("toggled", self._on_device_chosen, device.id)
            row.add_prefix(button)
            group.add(row)
            self._device_rows.append(row)

        if selected_id and all(device.id != selected_id for device in devices):
            # Keep a configured-but-absent device visible rather than making the
            # selection appear to have vanished.
            row = Adw.ActionRow(title="Configured device not found", subtitle=selected_id)
            group.add(row)
            self._device_rows.append(row)

        self._add_refresh_row(group)

    def _add_refresh_row(self, group: Adw.PreferencesGroup) -> None:
        row = Adw.ActionRow(
            title="Refresh devices",
            subtitle="Devices are refreshed automatically; use this to check now.",
        )
        button = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER)
        button.connect("clicked", self._on_refresh_devices)
        row.add_suffix(button)
        group.add(row)
        self._device_rows.append(row)

    def _on_refresh_devices(self, _button) -> None:
        self.manager.refresh_devices()
        self._refresh_device_rows()

    def _on_device_chosen(self, button, device_id: str | None) -> None:
        if not button.get_active():
            return
        settings = self.settings()
        device = dict(settings.get("device") or {})
        device["device_id"] = device_id
        settings["device"] = device
        self.set_settings(settings)
        self._last_signature = None
        self.render()


def _device_subtitle(device) -> str:
    parts = [device.device_type]
    if device.is_active:
        parts.append("active")
    if device.is_restricted:
        parts.append("restricted")
    if not device.supports_volume:
        parts.append("no volume control")
    return " · ".join(part for part in parts if part)


def migrate_settings(settings: dict) -> dict:
    """Bring stored settings up to the current schema.

    There is only one schema so far; the hook exists now so that the first
    change after release has somewhere to live instead of silently misreading
    older pages.
    """
    if not settings:
        return {}

    migrated = dict(settings)
    version = migrated.get("schema_version", 0)

    if version < 1:
        migrated["schema_version"] = SCHEMA_VERSION

    return migrated
