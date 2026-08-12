"""Spotify: User Information — who this deck is signed in as.

Uses the profile the manager fetched once after authentication; it never polls
`/me`, because the display name does not change while you are using a Stream
Deck.
"""

from __future__ import annotations

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from ..rendering import theme
from ..rendering.common import new_canvas, paste_artwork
from ..rendering.icons import paste_icon
from ..rendering.key import render_text_key
from ..spotify.state import ActionStatus
from .base import SpotifyActionBase


class UserInfoAction(SpotifyActionBase):
    TITLE = "Account"
    ICON = "user"
    USES_DEVICE_TARGET = False
    TOPICS = frozenset({"auth"})

    def register_events(self) -> None:
        self.add_event_assigner(
            EventAssigner(
                id="spotify_open_profile",
                ui_label="Open the profile in Spotify",
                default_events=[Input.Key.Events.SHORT_UP, Input.Dial.Events.SHORT_UP],
                callback=self._on_press,
            )
        )

    def _on_press(self, _data=None) -> None:
        profile = self.manager.profile
        target = profile.external_url if profile else None
        if not target or not self.manager.open_in_spotify(target):
            self.report_failure()

    def state_signature(self):
        profile = self.manager.profile
        return (
            self.manager.is_authenticated,
            profile.account_id if profile else None,
            profile.display_name if profile else None,
            bool(profile and self.manager.artwork.get(profile.image_url) is not None),
        )

    def render_image(self):
        if not self.manager.is_authenticated:
            return self.render_status(ActionStatus.AUTH_REQUIRED, detail="SIGN IN")

        profile = self.manager.profile
        if profile is None:
            return self.render_status(ActionStatus.PENDING, detail="…")

        size = self.image_size()
        width, height = size
        avatar = self.manager.artwork.get(profile.image_url) if profile.image_url else None
        name = profile.display_name or profile.account_id

        if avatar is None:
            return render_text_key(size, [name], color=theme.WHITE, accent="SPOTIFY")

        image, draw = new_canvas(size)
        avatar_band = int(height * 0.58)
        paste_artwork(image, avatar, (int(width * 0.24), int(height * 0.06), int(width * 0.76), avatar_band))

        from ..rendering.common import draw_centered_text, fit_font

        font = fit_font(draw, name, int(width * 0.92), int(height * 0.2), bold=True)
        draw_centered_text(draw, name, width / 2, avatar_band + int(height * 0.06), font, theme.WHITE)

        paste_icon(image, "success", int(height * 0.12), (int(width * 0.86), int(height * 0.12)), theme.SPOTIFY_GREEN)
        return image
