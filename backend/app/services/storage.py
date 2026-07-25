"""Configures where RetroHost reads ROMs from: local disk (default) or a
CIFS/Samba network share mounted on-demand on the Pi's operating system.

RetroArchDriver/PlayerService neither know nor need to know which mode is
active — they only receive game.launch_file, a regular file path. The component
that decides which directory to scan is effective_roms_dir(), used by the scanner.

Mounting/unmounting requires elevated privilege (mount -t cifs). The backend runs
`sudo mount`/`sudo umount` restricted by a minimal-scope sudoers rule
(see config/sudoers/homegames-mount, installed via scripts/setup_network_storage.sh)
— there is no unrestricted sudo inside the service process.
"""
import json
import subprocess
from pathlib import Path

from app.core.config import settings


class StorageConfigError(RuntimeError):
    pass


def get_storage_config() -> dict:
    if not settings.STORAGE_CONFIG_FILE.exists():
        return {"mode": "local", "cifs_host": None, "cifs_share": None, "cifs_username": None, "cifs_subpath": None}
    with open(settings.STORAGE_CONFIG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _write_storage_config(config: dict) -> None:
    settings.STORAGE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(settings.STORAGE_CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)


def _is_mounted() -> bool:
    try:
        result = subprocess.run(
            ["mountpoint", "-q", str(settings.CIFS_MOUNT_POINT)],
            check=False,
        )
    except OSError:
        # "mountpoint" does not exist on this OS (e.g. local dev on Windows) — only
        # the real Pi mounts CIFS, so treat as "not mounted".
        return False
    return result.returncode == 0


def _unmount_if_needed() -> None:
    if not _is_mounted():
        return
    try:
        subprocess.run(
            ["sudo", "umount", str(settings.CIFS_MOUNT_POINT)],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise StorageConfigError(f"Comando de desmontagem indisponível neste sistema: {exc}") from exc


def set_local_storage() -> None:
    try:
        _unmount_if_needed()
    except subprocess.CalledProcessError as exc:
        raise StorageConfigError(f"Falha ao desmontar o storage de rede: {exc.stderr}") from exc
    _write_storage_config(
        {"mode": "local", "cifs_host": None, "cifs_share": None, "cifs_username": None, "cifs_subpath": None}
    )


def set_cifs_storage(host: str, share: str, username: str, password: str, subpath: str = "") -> None:
    try:
        _unmount_if_needed()
    except subprocess.CalledProcessError as exc:
        raise StorageConfigError(f"Falha ao desmontar montagem anterior: {exc.stderr}") from exc

    credentials_content = f"username={username}\npassword={password}\n"
    try:
        subprocess.run(
            ["sudo", "tee", str(settings.CIFS_CREDENTIALS_PATH)],
            input=credentials_content,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["sudo", "chmod", "600", str(settings.CIFS_CREDENTIALS_PATH)], check=True)
    except subprocess.CalledProcessError as exc:
        raise StorageConfigError(f"Falha ao gravar credenciais: {exc.stderr}") from exc
    except OSError as exc:
        raise StorageConfigError(f"Comando de montagem indisponível neste sistema: {exc}") from exc

    mount_command = [
        "sudo", "mount", "-t", "cifs",
        f"//{host}/{share}", str(settings.CIFS_MOUNT_POINT),
        "-o", f"credentials={settings.CIFS_CREDENTIALS_PATH},uid=1000,gid=1000,vers={settings.CIFS_VERS}",
    ]
    try:
        subprocess.run(mount_command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise StorageConfigError(f"Falha ao montar //{host}/{share}: {exc.stderr}") from exc
    except OSError as exc:
        raise StorageConfigError(f"Comando de montagem indisponível neste sistema: {exc}") from exc

    _write_storage_config(
        {
            "mode": "cifs",
            "cifs_host": host,
            "cifs_share": share,
            "cifs_username": username,
            "cifs_subpath": subpath or None,
        }
    )


def _mount_from_config(config: dict) -> None:
    """Mount CIFS from a storage config. Raises StorageConfigError with a clear
    message on failure. Assumes credentials are already written."""
    host, share = config.get("cifs_host"), config.get("cifs_share")
    mount_command = [
        "sudo", "mount", "-t", "cifs",
        f"//{host}/{share}", str(settings.CIFS_MOUNT_POINT),
        "-o", f"credentials={settings.CIFS_CREDENTIALS_PATH},uid=1000,gid=1000,vers={settings.CIFS_VERS}",
    ]
    try:
        subprocess.run(mount_command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise StorageConfigError(f"Falha ao montar //{host}/{share}: {exc.stderr}") from exc
    except OSError as exc:
        raise StorageConfigError(f"Comando de montagem indisponível: {exc}") from exc


def _can_remount() -> bool:
    """True if storage is configured for CIFS, not mounted, and remountable."""
    config = get_storage_config()
    return (
        config.get("mode") == "cifs"
        and not _is_mounted()
        and settings.CIFS_CREDENTIALS_PATH.exists()
        and bool(config.get("cifs_host"))
        and bool(config.get("cifs_share"))
    )


def remount_if_configured() -> None:
    """Remount CIFS on container boot if configured for network (silent).

    The CIFS mount lives in the container namespace and does NOT survive a restart.
    storage.json and credentials persist in a volume; this remounts without the
    user having to reconfigure. Silent failure: does not bring down the backend
    boot if the server is offline.
    """
    if not _can_remount():
        return
    try:
        _mount_from_config(get_storage_config())
    except StorageConfigError:
        return


def ensure_storage_mounted() -> None:
    """Ensure CIFS is mounted before starting a game (on-demand).

    The mount can drop during use (Wi-Fi, inactivity timeout). Called by
    PlayerService.play(); if storage is CIFS and has dropped, remounts — and
    propagates StorageConfigError (becomes HTTP/UI error) if it can't, instead
    of the game failing with 'Failed to load content' with no explanation. No-op
    if local or already mounted.
    """
    if _can_remount():
        _mount_from_config(get_storage_config())


def effective_roms_dir() -> Path:
    config = get_storage_config()
    if config["mode"] == "cifs":
        subpath = config.get("cifs_subpath")
        if subpath:
            return settings.CIFS_MOUNT_POINT / subpath
        return settings.CIFS_MOUNT_POINT
    return settings.ROMS_DIR
