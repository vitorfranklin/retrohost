"""Remote input WebSocket: the browser sends gamepad button events,
the backend translates them into keystrokes on the virtual uinput keyboard (see app/input/).

Only ONE active client at a time — when a new one connects, the previous is
closed with code 4000 ("control taken over"). The frontend detects this code
and returns to the idle view without stopping the game.

No authentication — personal project for home use on a LAN, single user.
"""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.player import get_player_service

router = APIRouter()

_active_ws: WebSocket | None = None


@router.websocket("/ws/input")
async def input_ws(websocket: WebSocket) -> None:
    global _active_ws
    await websocket.accept()

    # Close the previous client, if any, signalling that control was taken over.
    previous = _active_ws
    _active_ws = websocket
    if previous is not None:
        try:
            await previous.close(code=4000)
        except Exception:
            pass

    player = get_player_service()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                key = msg["key"]
                pressed = bool(msg["pressed"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            player.handle_input(key, pressed)
    except WebSocketDisconnect:
        pass
    finally:
        if _active_ws is websocket:
            _active_ws = None
        player.release_all_inputs()
