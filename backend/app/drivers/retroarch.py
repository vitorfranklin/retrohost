"""Concrete driver that runs games via RetroArch (subprocess).

The console -> libretro core mapping is read from config/cores.json. If the core
configured for the game's console does not exist on disk, we raise
CoreNotConfiguredError instead of trying to run — the player service translates
this into a friendly HTTP response.

RetroArch always runs headless (no HDMI/physical display): it records raw video
+ PCM audio into a named pipe (FIFO) via --recordconfig, which FFmpegStreamingProvider
reads on the other end to encode and publish via WebRTC. video_driver=null,
input_driver=udev and audio_driver=alsa must be configured in retroarch.cfg on
the Pi (see scripts/setup_streaming.sh) — without this RetroArch fails to
initialize or runs in "fast forward" (audio_sync without real audio).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.drivers.base import CoreNotConfiguredError, EmulatorDriver

# PS1 BIOS filenames that the Beetle PSX core looks for (JP/US/EU). We copy the
# BIOS found in storage using all three names to cover any ROM region.
_PS1_BIOS_NAMES = ("scph5500.bin", "scph5501.bin", "scph5502.bin")


class RetroArchDriver(EmulatorDriver):
    def __init__(self, retroarch_bin: str = "retroarch") -> None:
        self.retroarch_bin = retroarch_bin
        self._process: subprocess.Popen | None = None

    def _load_core_map(self) -> dict[str, str]:
        if not settings.CORES_CONFIG_FILE.exists():
            return {}
        with open(settings.CORES_CONFIG_FILE, encoding="utf-8") as fh:
            return json.load(fh)

    def _resolve_core(self, console: str) -> Path:
        core_map = self._load_core_map()
        core_path = core_map.get(console)
        if not core_path or not Path(core_path).is_file():
            raise CoreNotConfiguredError(
                f"Core para o console '{console}' ainda não foi configurado. "
                f"Adicione o caminho do core .so em {settings.CORES_CONFIG_FILE} "
                f"e coloque o arquivo em {settings.CORES_DIR}."
            )
        return Path(core_path)

    def _ensure_ps1_bios(self, game: Any) -> None:
        """Copy the PS1 BIOS from storage to SYSTEM_DIR, if needed.

        The Beetle PSX core (used in the x86 container) requires a BIOS — unlike
        pcsx-rearmed on the Pi, which has HLE BIOS. We look for a scph*.bin in the
        same storage as the ROMs (effective_roms_dir) and copy it with the names the
        core expects. No-op if SYSTEM_DIR is not configured (e.g. on the Pi) or if
        the BIOS is already there. Failures here do not raise — RetroArch will give
        a clear error message.
        """
        if not settings.SYSTEM_DIR or game.console != "ps1":
            return
        system_dir = Path(settings.SYSTEM_DIR)
        if (system_dir / _PS1_BIOS_NAMES[1]).exists():
            return  # already copied
        from app.services.storage import effective_roms_dir

        try:
            bios_src = next(effective_roms_dir().rglob("[Ss][Cc][Pp][Hh]*.[Bb][Ii][Nn]"), None)
            if bios_src is None:
                return
            system_dir.mkdir(parents=True, exist_ok=True)
            for name in _PS1_BIOS_NAMES:
                shutil.copy(bios_src, system_dir / name)
        except OSError:
            return

    def _resolve_launch_file(self, launch_file: str) -> str:
        """Fix .cue files with broken FILE references (absolute Windows path).

        ROMs dumped on Windows PCs (ePSXe etc.) write paths like
        FILE "C:\\CRASH 2.BIN" or a full absolute path in the .cue — which do not
        exist on Linux. If the reference doesn't exist but there is a file with the
        same base name (or a single .bin/.iso) next to the .cue, we generate a
        corrected .cue in a tmp path pointing to the real file (without modifying
        the original in storage). Returns the path to use (the corrected tmp, or
        the original if it was already valid).
        """
        path = Path(launch_file)
        if path.suffix.lower() != ".cue":
            return launch_file
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            return launch_file

        game_dir = path.parent
        changed = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.upper().startswith("FILE "):
                # extract the filename between quotes
                start, end = line.find('"'), line.rfind('"')
                if start != -1 and end > start:
                    ref = line[start + 1:end]
                    ref_basename = ref.replace("\\", "/").rsplit("/", 1)[-1]
                    # Fix if: (a) the ref has an embedded path (Windows "C:\..." or
                    # absolute "/..."), which the core would try to open literally, or
                    # (b) the basename doesn't exist next to the .cue. Otherwise,
                    # keep it (ref is already a valid relative name, as in Tarzan).
                    has_path = ("\\" in ref) or (":" in ref) or ("/" in ref)
                    if has_path or not (game_dir / ref_basename).is_file():
                        replacement = self._find_track_file(game_dir, ref_basename)
                        if replacement is not None:
                            line = line[:start + 1] + replacement + line[end:]
                            changed = True
            new_lines.append(line)

        if not changed:
            return launch_file
        # The corrected .cue MUST live in the same directory as the track files:
        # the core (Mednafen/Beetle PSX) resolves FILE lines relative to the .cue's
        # directory, not cwd. We write a sibling .cue with a prefix. If the directory
        # is read-only, we fall back to the original (the core will give a clear error).
        fixed_cue = path.with_name(f".homegames_{path.name}")
        try:
            fixed_cue.write_text("\n".join(new_lines) + "\n")
        except OSError:
            return launch_file
        return str(fixed_cue)

    @staticmethod
    def _find_track_file(game_dir: Path, ref_basename: str) -> str | None:
        """Find the real track file for a broken FILE reference."""
        stem = Path(ref_basename).stem
        # 1) same base name, any disk image extension
        for ext in (".bin", ".iso", ".img"):
            for candidate in game_dir.glob("*"):
                if candidate.stem.lower() == stem.lower() and candidate.suffix.lower() == ext:
                    return candidate.name
        # 2) fallback: if there is a single .bin/.iso in the folder, use it
        images = [p for p in game_dir.iterdir()
                  if p.suffix.lower() in (".bin", ".iso", ".img")]
        if len(images) == 1:
            return images[0].name
        return None

    def launch(self, game: Any) -> None:
        if self.status():
            raise RuntimeError("Já existe um jogo em execução. Pare-o antes de iniciar outro.")

        core_path = self._resolve_core(game.console)
        self._ensure_ps1_bios(game)
        launch_file = self._resolve_launch_file(game.launch_file)

        fifo_path = settings.STREAMING_FIFO_PATH
        if fifo_path.exists():
            os.remove(fifo_path)
        os.mkfifo(fifo_path)

        command = [
            self.retroarch_bin, "-f",
            "-r", str(fifo_path),
            "--recordconfig", str(settings.RECORDCONFIG_PATH),
            "-L", str(core_path),
            launch_file,
        ]
        # cwd = game directory: .cue-based cores (e.g. Beetle PSX)
        # resolve track files (.bin) relative to cwd, not the .cue.
        # Without this, "Failed to load content" when running from another directory.
        launch_dir = Path(game.launch_file).parent
        cwd = str(launch_dir) if launch_dir.is_dir() else None
        self._process = subprocess.Popen(command, cwd=cwd, env=self._launch_env())

    def _launch_env(self) -> dict[str, str] | None:
        """RetroArch environment. In SDL input mode (container), injects the
        virtual joystick LD_PRELOAD and forces SDL not to open real video/audio."""
        if settings.INPUT_PROVIDER != "sdl" or not settings.INPUT_SDL_PRELOAD:
            return None
        env = dict(os.environ)
        preload = settings.INPUT_SDL_PRELOAD
        if env.get("LD_PRELOAD"):
            preload = f"{env['LD_PRELOAD']}:{preload}"
        env["LD_PRELOAD"] = preload
        env.setdefault("SDL_VIDEODRIVER", "dummy")
        env.setdefault("SDL_AUDIODRIVER", "dummy")
        env["HOMEGAMES_INPUT_SOCK"] = settings.INPUT_SOCK_PATH
        return env

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._process = None

        fifo_path = settings.STREAMING_FIFO_PATH
        if fifo_path.exists():
            os.remove(fifo_path)

    def status(self) -> bool:
        if self._process is None:
            return False
        if self._process.poll() is None:
            return True
        self._process = None
        return False
