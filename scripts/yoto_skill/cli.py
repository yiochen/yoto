from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable

import httpx

from .api import YotoAPI
from .auth import AuthManager, DEFAULT_AUTH_BASE, DEFAULT_REMOTE_CALLBACK_URL
from .covers import download_image, prepare_cover
from .errors import YotoError
from .icons import prepare_icon
from .media import extract_cover, inspect_mp3
from .service import YotoService
from .storage import ConfigStore, KeychainTokenStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="yoto", description="Manage Yoto MYO cards and songs")
    root.add_argument("--json", action="store_true", dest="as_json", help="Print a stable JSON response envelope")
    categories = root.add_subparsers(dest="category", required=True)

    auth = categories.add_parser("auth")
    auth_commands = auth.add_subparsers(dest="command", required=True)
    configure = auth_commands.add_parser("configure")
    configure.add_argument("--client-id", required=True)
    begin = auth_commands.add_parser("begin", help="Begin authorization on another device")
    begin.add_argument("--callback-url", default=DEFAULT_REMOTE_CALLBACK_URL)
    auth_commands.add_parser("complete", help="Read the copied authorization JSON from standard input")
    auth_commands.add_parser("login")
    auth_commands.add_parser("status")
    auth_commands.add_parser("logout")

    card = categories.add_parser("cards")
    card_commands = card.add_subparsers(dest="command", required=True)
    card_commands.add_parser("list")
    show = card_commands.add_parser("show")
    show.add_argument("--card-id", required=True)
    create = card_commands.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--description")
    set_cover = card_commands.add_parser("set-cover")
    set_cover.add_argument("--card-id", required=True)
    set_cover.add_argument("--file", required=True, help="Prepared 638x1011 PNG from media prepare-cover")

    media = categories.add_parser("media")
    media_commands = media.add_subparsers(dest="command", required=True)
    inspect = media_commands.add_parser("inspect")
    inspect.add_argument("--file", required=True)
    embedded_cover = media_commands.add_parser("cover")
    embedded_cover.add_argument("--file", required=True)
    prepare_cover_command = media_commands.add_parser("prepare-cover")
    cover_source = prepare_cover_command.add_mutually_exclusive_group(required=True)
    cover_source.add_argument("--file")
    cover_source.add_argument("--image-url")
    prepare_cover_command.add_argument("--part-number", type=int)
    prepare_cover_command.add_argument("--part-total", type=int)
    prepare_cover_command.add_argument("--header")
    prepare_cover_command.add_argument("--title")
    prepare_cover_command.add_argument("--subtitle")
    prepare_cover_command.add_argument("--background", default="#172030")
    prepare_cover_command.add_argument("--foreground", default="#FFFFFF")

    icon = categories.add_parser("icons")
    icon_commands = icon.add_subparsers(dest="command", required=True)
    prepare = icon_commands.add_parser("prepare")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--title", required=True)

    song = categories.add_parser("songs")
    song_commands = song.add_subparsers(dest="command", required=True)
    add = song_commands.add_parser("add")
    add.add_argument("--card-id", required=True)
    add.add_argument("--file", required=True)
    add.add_argument("--icon-file", required=True)
    add.add_argument("--title")
    rename = song_commands.add_parser("rename")
    rename.add_argument("--card-id", required=True)
    rename.add_argument("--song-key", required=True)
    rename.add_argument("--title", required=True)
    remove = song_commands.add_parser("remove")
    remove.add_argument("--card-id", required=True)
    remove.add_argument("--song-key", required=True)
    remove.add_argument("--yes", action="store_true")
    move = song_commands.add_parser("move")
    move.add_argument("--card-id", required=True)
    move.add_argument("--song-key", required=True)
    move.add_argument("--position", type=int, required=True)
    set_icon = song_commands.add_parser("set-icon")
    set_icon.add_argument("--card-id", required=True)
    set_icon.add_argument("--song-key", required=True)
    set_icon.add_argument("--icon-file", required=True)
    return root


def dependencies() -> tuple[AuthManager, YotoService]:
    auth_http = httpx.Client(timeout=30)
    auth = AuthManager(
        config=ConfigStore(),
        tokens=KeychainTokenStore(),
        http=auth_http,
        auth_base=os.environ.get("YOTO_AUTH_BASE_URL", DEFAULT_AUTH_BASE),
    )
    api = YotoAPI(auth, http=httpx.Client(timeout=60))
    return auth, YotoService(api)


def execute(args: argparse.Namespace, auth: AuthManager, service: YotoService) -> Any:
    if args.category == "auth":
        if args.command == "configure":
            return auth.configure(args.client_id)
        if args.command == "begin":
            return auth.begin(args.callback_url)
        if args.command == "complete":
            return auth.complete(_read_auth_result())
        if args.command == "login":
            return auth.login()
        if args.command == "status":
            return auth.status()
        if args.command == "logout":
            return auth.logout()
    if args.category == "cards":
        operations = {
            "list": service.list_cards,
            "show": lambda: service.show_card(args.card_id),
            "create": lambda: service.create_card(args.title, args.description),
            "set-cover": lambda: service.set_cover(args.card_id, args.file),
        }
        return operations[args.command]()
    if args.category == "media" and args.command == "inspect":
        return inspect_mp3(args.file).json()
    if args.category == "media" and args.command == "cover":
        return extract_cover(args.file)
    if args.category == "media" and args.command == "prepare-cover":
        source = download_image(args.image_url) if args.image_url else args.file
        return prepare_cover(source, part_number=args.part_number, part_total=args.part_total,
                             header=args.header, title=args.title, subtitle=args.subtitle,
                             background=args.background, foreground=args.foreground).json()
    if args.category == "icons" and args.command == "prepare":
        return prepare_icon(args.input, args.title).json()
    if args.category == "songs":
        operations = {
            "add": lambda: service.add_song(args.card_id, args.file, args.icon_file, args.title),
            "rename": lambda: service.rename_song(args.card_id, args.song_key, args.title),
            "remove": lambda: service.remove_song(args.card_id, args.song_key, args.yes),
            "move": lambda: service.move_song(args.card_id, args.song_key, args.position),
            "set-icon": lambda: service.set_icon(args.card_id, args.song_key, args.icon_file),
        }
        return operations[args.command]()
    raise YotoError("unknown_command", "Unknown command")


def _read_auth_result() -> str:
    result = sys.stdin.read()
    if not result.strip():
        raise YotoError(
            "missing_auth_result",
            "Paste the copied authorization JSON into standard input",
        )
    return result


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        print(json.dumps(payload["data"], indent=2, sort_keys=True))
    else:
        print(f"Error [{payload['error']['code']}]: {payload['error']['message']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        auth, service = dependencies()
        data = execute(args, auth, service)
        emit({"ok": True, "data": data, "warnings": []}, args.as_json)
        return 0
    except YotoError as exc:
        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            error["details"] = exc.details
        emit({"ok": False, "error": error}, args.as_json)
        return exc.exit_code
    except KeyboardInterrupt:
        emit({"ok": False, "error": {"code": "interrupted", "message": "Operation interrupted"}}, args.as_json)
        return 130
    except Exception as exc:  # Keep automation output machine-readable for unexpected local failures.
        emit(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "The local Yoto helper failed unexpectedly",
                    "details": {"exception": type(exc).__name__},
                },
            },
            args.as_json,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
