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
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.drivers.base import CoreNotConfiguredError, EmulatorDriver

# BIOS handling per console. `bios_subdir`: relative to SYSTEM_DIR — most
# cores (Beetle PSX) read BIOS directly from SYSTEM_DIR, but pcsx2_libretro
# looks in "pcsx2/bios/" specifically (found empirically: the core reports
# "Could not find any valid PS2 BIOS File" otherwise, even with the BIOS
# present in SYSTEM_DIR itself). `bios_names`: filenames the core expects —
# copied from the first scph*.bin found in ROM storage (see _ensure_bios),
# each a separate copy of the same source file, since Beetle PSX matches by
# exact filename per region (JP/US/EU). None means the core doesn't care
# about the filename and identifies the BIOS by content instead (pcsx2_libretro
# scans its bios dir and detects any valid dump regardless of name) — in that
# case we keep the original filename found in storage.
_BIOS_CONFIG: dict[str, dict[str, Any]] = {
    "ps1": {
        "bios_names": ("scph5500.bin", "scph5501.bin", "scph5502.bin"),
        "bios_subdir": "",
    },
    "ps2": {
        "bios_names": None,
        "bios_subdir": "pcsx2/bios",
    },
}

# RetroArch video profile per console. Default (any console not listed) keeps
# today's behavior: video_driver=null, no display/context needed — sufficient
# for 2D-era cores and PS1 (Beetle PSX), which never open a real graphics
# context and only get captured via --recordconfig into the FIFO.
#
# PS2 (pcsx2_libretro) is RETRO_HW_RENDER: it needs a real OpenGL context to
# draw at all, so video_driver=null is not an option — confirmed in the
# poc/ps2-support-headless-gl POC (docs/ps2-poc.md). video_gpu_record=true is
# also required for HW-rendered cores: without it, RetroArch silently never
# writes to the FIFO (logs a warning, but --recordconfig alone is not enough).
#
# recording_output_directory: PS2 needs its own dedicated dir (see
# CONSOLES_WITH_UNSTABLE_FIFO below) — pinned here so both the driver (which
# clears it per-launch) and the video profile (which tells RetroArch to write
# there) agree on the same path without a second source of truth.
_PS2_RECORDS_DIR = "/tmp/retrohost_ps2_records"
_VIDEO_PROFILES: dict[str, dict[str, str]] = {
    "ps2": {
        "video_driver": "gl",
        "video_context_driver": "x11",
        "video_fullscreen": "true",
        "video_gpu_record": "true",
        "recording_output_directory": _PS2_RECORDS_DIR,
    },
}

# Consoles where RetroArch does NOT reliably keep writing to the FIFO passed
# via -r for the whole session. Found empirically for PS2 (pcsx2_libretro):
# the core re-emits SET_SYSTEM_AV_INFO twice during BIOS boot (before the
# actual game starts), and each time RetroArch restarts its recording
# subsystem — but after the restart it silently falls back to a timestamped
# .mkv file in recording_output_directory instead of continuing to write to
# the FIFO given via -r. PS1/SNES never hit this (fixed geometry from the
# first frame), so they keep using the simple FIFO path. For consoles in this
# set, FFmpegStreamingProvider reads the last (still-growing) .mkv file
# instead of the FIFO (see _source_for in streaming/ffmpeg_webrtc.py) — this
# driver's job is only to clear the records dir per-launch (_clear_records_dir)
# and keep a throwaway reader on the now-unused FIFO so RetroArch's -r doesn't
# block forever (see the _fifo_sink comment in launch()). See docs/ps2-poc.md.
CONSOLES_WITH_UNSTABLE_FIFO: dict[str, str] = {
    "ps2": _PS2_RECORDS_DIR,
}


class RetroArchDriver(EmulatorDriver):
    def __init__(self, retroarch_bin: str = "retroarch") -> None:
        self.retroarch_bin = retroarch_bin
        self._process: subprocess.Popen | None = None
        self._video_config_path: str | None = None
        self._fifo_sink: subprocess.Popen | None = None

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
                f"Core for console '{console}' has not been configured yet. "
                f"Add the .so core path to {settings.CORES_CONFIG_FILE} "
                f"and place the file in {settings.CORES_DIR}."
            )
        return Path(core_path)

    def _ensure_bios(self, game: Any) -> None:
        """Copy the console's BIOS from storage to SYSTEM_DIR, if needed.

        Generalizes what used to be PS1-only (_ensure_ps1_bios): consoles in
        _BIOS_CONFIG need a real BIOS file next to the core (unlike pcsx-rearmed
        on the Pi, which has HLE BIOS and needs none). We look for a scph*.bin in
        the same storage as the ROMs (effective_roms_dir) and copy it with the
        names/subdir the core expects. No-op if SYSTEM_DIR is not configured
        (e.g. on the Pi) or if the console needs no BIOS. Failures here do not
        raise — RetroArch will give a clear error message.
        """
        bios_config = _BIOS_CONFIG.get(game.console)
        if not settings.SYSTEM_DIR or bios_config is None:
            return
        bios_dir = Path(settings.SYSTEM_DIR) / bios_config["bios_subdir"]
        bios_names: tuple[str, ...] | None = bios_config["bios_names"]
        if bios_names and (bios_dir / bios_names[0]).exists():
            return  # already copied
        from app.services.storage import effective_roms_dir

        try:
            bios_src = next(effective_roms_dir().rglob("[Ss][Cc][Pp][Hh]*.[Bb][Ii][Nn]"), None)
            if bios_src is None:
                return
            bios_dir.mkdir(parents=True, exist_ok=True)
            if bios_names:
                for name in bios_names:
                    shutil.copy(bios_src, bios_dir / name)
            elif not (bios_dir / bios_src.name).exists():
                shutil.copy(bios_src, bios_dir / bios_src.name)
        except OSError:
            return

    def _write_video_profile_config(self, console: str) -> str | None:
        """Write a temporary --appendconfig file with the console's video profile.

        RetroArch's own retroarch.cfg is generated once at container boot
        (scripts/docker-entrypoint.sh) with video_driver=null, shared by every
        console. Consoles that need a real graphics context (PS2) override it
        per-process via --appendconfig, which RetroArch documents as taking
        priority over the base config — this avoids rewriting the shared .cfg
        (and the race that would create) for every launch. Returns None for
        consoles that don't need an override (keeps today's null-driver path).
        """
        profile = _VIDEO_PROFILES.get(console)
        if profile is None:
            return None
        lines = [f'{key} = "{value}"' for key, value in profile.items()]
        fd, path = tempfile.mkstemp(prefix="retrohost_video_", suffix=".cfg")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

    @staticmethod
    def _clear_records_dir(records_dir: str) -> None:
        """Remove leftover .mkv files from a previous session, if any.

        Each PS2 launch produces 2-3 recording restarts during BIOS boot (see
        CONSOLES_WITH_UNSTABLE_FIFO) before RetroArch settles on the file it
        keeps writing to for the rest of the session — the orphaned files from
        the restarts are never cleaned up by RetroArch itself and would
        otherwise accumulate (~100-400MB each) and make it ambiguous which
        file from *this* launch is the active one. FFmpegStreamingProvider
        relies on this directory starting empty to find the right file (see
        streaming/ffmpeg_webrtc.py).
        """
        path = Path(records_dir)
        path.mkdir(parents=True, exist_ok=True)
        for leftover in path.glob("*.mkv"):
            leftover.unlink(missing_ok=True)

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
            raise RuntimeError("A game is already running. Stop it before starting another one.")

        core_path = self._resolve_core(game.console)
        self._ensure_bios(game)
        launch_file = self._resolve_launch_file(game.launch_file)
        video_config_path = self._write_video_profile_config(game.console)
        self._video_config_path = video_config_path

        records_dir = CONSOLES_WITH_UNSTABLE_FIFO.get(game.console)
        if records_dir:
            self._clear_records_dir(records_dir)

        fifo_path = settings.STREAMING_FIFO_PATH
        if fifo_path.exists():
            os.remove(fifo_path)
        os.mkfifo(fifo_path)

        # RetroArch's open() on the FIFO (write end, via -r) blocks until a
        # reader connects — normally FFmpegStreamingProvider is that reader.
        # For CONSOLES_WITH_UNSTABLE_FIFO the real stream comes from the .mkv
        # instead (see streaming/ffmpeg_webrtc.py), so nothing else would ever
        # open the FIFO for reading and RetroArch would hang indefinitely
        # before even reaching the BIOS. -r is still required on the CLI (no
        # way to make the core skip recording entirely), so we keep a
        # throwaway reader alive for the whole session purely to unblock that
        # open() — its bytes are discarded, they are never the real source.
        self._fifo_sink = None
        if records_dir:
            self._fifo_sink = subprocess.Popen(
                ["cat", str(fifo_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        command = [
            self.retroarch_bin, "-f",
            "-r", str(fifo_path),
            "--recordconfig", str(settings.RECORDCONFIG_PATH),
            "-L", str(core_path),
        ]
        if video_config_path:
            command += ["--appendconfig", video_config_path]
        command.append(launch_file)
        # cwd = game directory: .cue-based cores (e.g. Beetle PSX)
        # resolve track files (.bin) relative to cwd, not the .cue.
        # Without this, "Failed to load content" when running from another directory.
        launch_dir = Path(game.launch_file).parent
        cwd = str(launch_dir) if launch_dir.is_dir() else None
        self._process = subprocess.Popen(command, cwd=cwd, env=self._launch_env(game.console))

    def _launch_env(self, console: str) -> dict[str, str] | None:
        """RetroArch environment. In SDL input mode (container), injects the
        virtual joystick LD_PRELOAD and forces SDL not to open real video/audio
        — except for consoles with a real video profile (PS2), which need a
        real X display (Xvfb) for their OpenGL context; forcing SDL_VIDEODRIVER
        to dummy there would be pointless (video goes through GLX/X11, not
        SDL) but DISPLAY must still be set for GLX to find the Xvfb server.
        """
        needs_real_display = console in _VIDEO_PROFILES
        if settings.INPUT_PROVIDER != "sdl" or not settings.INPUT_SDL_PRELOAD:
            if needs_real_display:
                env = dict(os.environ)
                env.setdefault("DISPLAY", settings.X_DISPLAY)
                return env
            return None
        env = dict(os.environ)
        preload = settings.INPUT_SDL_PRELOAD
        if env.get("LD_PRELOAD"):
            preload = f"{env['LD_PRELOAD']}:{preload}"
        env["LD_PRELOAD"] = preload
        if needs_real_display:
            env.setdefault("DISPLAY", settings.X_DISPLAY)
        else:
            env.setdefault("SDL_VIDEODRIVER", "dummy")
            env.setdefault("SDL_AUDIODRIVER", "dummy")
        env["HOMEGAMES_INPUT_SOCK"] = settings.INPUT_SOCK_PATH
        return env

    def stop(self) -> None:
        if self._video_config_path:
            Path(self._video_config_path).unlink(missing_ok=True)
            self._video_config_path = None
        if self._fifo_sink is not None:
            if self._fifo_sink.poll() is None:
                self._fifo_sink.terminate()
                try:
                    self._fifo_sink.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._fifo_sink.kill()
                    self._fifo_sink.wait()
            self._fifo_sink = None
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
