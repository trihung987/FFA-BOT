"""Compatibility facade for split Discord UI view modules."""

from view_modules.common import (
    _load_player_map,
    _load_player_ticket_map,
    _safe_defer,
    _safe_edit,
    _safe_edit_original_response,
    _safe_message_edit,
    _safe_send,
    _set_view_items_disabled,
    build_checkin_embed,
    build_registered_mentions,
    build_registration_embed,
)
from view_modules.registration import (
    CheckInView,
    MapNamesModal,
    RegistrationView,
    build_disabled_checkin_view,
    build_disabled_registration_view,
)
from view_modules.results import (
    LobbyResultView,
    ScoreModal,
    _build_lobby_score_image_filename,
    _safe_int_score,
    build_lobby_result_embed,
    build_lobby_result_image_file,
    build_lobby_result_message_assets,
)

__all__ = [
    "_load_player_map",
    "_load_player_ticket_map",
    "_safe_defer",
    "_safe_edit",
    "_safe_edit_original_response",
    "_safe_message_edit",
    "_safe_send",
    "_set_view_items_disabled",
    "build_checkin_embed",
    "build_disabled_checkin_view",
    "build_disabled_registration_view",
    "build_lobby_result_embed",
    "build_lobby_result_image_file",
    "build_lobby_result_message_assets",
    "build_registered_mentions",
    "build_registration_embed",
    "CheckInView",
    "LobbyResultView",
    "MapNamesModal",
    "RegistrationView",
    "ScoreModal",
    "_build_lobby_score_image_filename",
    "_safe_int_score",
]
