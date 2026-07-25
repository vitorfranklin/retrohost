import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.drivers.base import CoreNotConfiguredError
from app.models.game import Game
from app.schemas.game import GameRead, PlayRequest, ScanResult, StatusResponse
from app.schemas.storage import SetCifsStorageRequest, StorageConfig
from app.services.player import StreamNotReadyError, get_player_service
from app.services.scanner import clear_games, scan_roms
from app.services.storage import (
    StorageConfigError,
    effective_roms_dir,
    get_storage_config,
    set_cifs_storage,
    set_local_storage,
)

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/games", response_model=list[GameRead])
def list_games(db: Session = Depends(get_db)) -> list[Game]:
    return db.query(Game).order_by(Game.title).all()


@router.get("/games/{game_id}", response_model=GameRead)
def get_game(game_id: int, db: Session = Depends(get_db)) -> Game:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@router.post("/scan", response_model=ScanResult)
def scan_library(db: Session = Depends(get_db)) -> dict:
    return scan_roms(db)


@router.get("/storage", response_model=StorageConfig)
def get_storage() -> dict:
    return get_storage_config()


@router.post("/storage/local", response_model=ScanResult)
def use_local_storage(db: Session = Depends(get_db)) -> dict:
    set_local_storage()
    clear_games(db)
    return scan_roms(db, roms_dir=effective_roms_dir())


@router.post("/storage/cifs", response_model=ScanResult)
def use_cifs_storage(request: SetCifsStorageRequest, db: Session = Depends(get_db)) -> dict:
    try:
        set_cifs_storage(request.host, request.share, request.username, request.password, request.subpath)
    except StorageConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    clear_games(db)
    return scan_roms(db, roms_dir=effective_roms_dir())


@router.post("/play", response_model=StatusResponse)
def play(request: PlayRequest, db: Session = Depends(get_db)) -> StatusResponse:
    game = db.get(Game, request.game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    player = get_player_service()
    try:
        player.play(game)
    except CoreNotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorageConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except StreamNotReadyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    running, current_game = player.status()
    return StatusResponse(running=running, game=GameRead.model_validate(current_game) if current_game else None)


@router.post("/stop", response_model=StatusResponse)
def stop() -> StatusResponse:
    player = get_player_service()
    player.stop()
    return StatusResponse(running=False, game=None)


@router.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    player = get_player_service()
    running, current_game = player.status()
    return StatusResponse(running=running, game=GameRead.model_validate(current_game) if current_game else None)


@router.get("/config")
def get_config(request: Request) -> dict:
    cores: dict = {}
    if settings.CORES_CONFIG_FILE.exists():
        with open(settings.CORES_CONFIG_FILE, encoding="utf-8") as fh:
            cores = json.load(fh)
    host = request.url.hostname
    whep_url = f"http://{host}:{settings.MEDIAMTX_WHEP_PORT}{settings.MEDIAMTX_WHEP_PATH}"
    return {
        "roms_dir": str(effective_roms_dir()),
        "cores_dir": str(settings.CORES_DIR),
        "configured_cores": cores,
        "whep_url": whep_url,
        "storage": get_storage_config(),
    }
