# POC: headless PS2 rendering (Xvfb + PCSX2 libretro core)

Branch: `poc/ps2-support-headless-gl`
Related issue: [#20](https://github.com/vitorfranklin/retrohost/issues/20)

## Question This POC Answers

`video_driver=null` works for PS1/SNES because those cores do not open any
graphics context; RetroArch writes directly to the FIFO via `--recordconfig`.
The PS2 core (`pcsx2_libretro.so`) requires a real OpenGL/Vulkan context to
render.

**Question:** inside RetroHost's x86_64 Docker container, can we give the core
that real context (via Xvfb, without a physical display) and still capture
frames through the same FIFO -> ffmpeg pipeline we already use?

## Out Of Scope

To isolate the unknown variable (rendering), this POC does **not** touch:

- `PlayerService` / API / database
- `RetroArchDriver` (the smoke test runs RetroArch directly, outside the app)
- Input (analog, DualShock, etc.)
- BIOS (accepts BIOS via volume, but does not implement generalized
  `_ensure_ps1_bios` logic)
- Multi-disc handling
- `cores.json` / `VALID_ROM_EXTENSIONS` / library scanner

If the smoke test proves rendering is viable, these items become the scope for
POC #2 (end-to-end integration).

## POC Files

- `Dockerfile.ps2-poc` - isolated image, not the production image. Installs
  RetroArch + Xvfb + Mesa and downloads `pcsx2_libretro.so` from the official
  libretro buildbot (there is no Ubuntu 24.04 apt package).
- `scripts/ps2-poc/render-smoketest.sh` - entrypoint: starts Xvfb, configures
  `video_driver=gl` / `video_context_driver=x11` instead of `null`, runs the
  core against a user-provided ISO, and measures bytes received by the FIFO.
- `compose.ps2-poc.yml` - compose file isolated from the production
  `compose.yml`.

## How To Run

BIOS and ISO files are **not provided** by this repository due to copyright.
Use a PS2 BIOS and an ISO you legally own.

```bash
docker compose -f compose.ps2-poc.yml build

PS2_BIOS_DIR=/path/to/bios \
PS2_ISO_PATH=/path/to/game.iso \
  docker compose -f compose.ps2-poc.yml up
```

Without `PS2_ISO_PATH`, the script still starts the core without content
(`--menu`). That is useful as an initial signal that the core loads and does not
crash, but it does not prove that real game frames are rendered.

To test with an Intel/AMD GPU (VAAPI/DRI) instead of Mesa llvmpipe (software),
uncomment the `devices: [/dev/dri:/dev/dri]` block in `compose.ps2-poc.yml`.

## Success Criteria

The script prints a final result:

```text
=== RESULT ===
RetroArch exit code : ...
Bytes received by FIFO : N
glxinfo renderer : ...
```

- **Bytes received > 0** with a real ISO = strong viability signal. The core
  rendered and the existing FIFO pipeline captured frames without modification.
- **Bytes received > 0** with `--menu` only (no ISO) = partial signal. GL works,
  but this still does not prove the full game path.
- **0 bytes / crash** = inspect the log (`/tmp/retroarch.log` inside the
  container). This can indicate the need for EGL surfaceless instead of Xvfb, a
  missing Mesa driver, or a nightly core version issue.

## Phase 2a Result: Rendering Confirms Bytes But Not Image (2026-07-30)

**Important correction to the original 2026-07-29 conclusion:** the 2026-07-29
result confirmed "bytes > 0 in the FIFO" and GLSL shader compilation, and that
was interpreted as success. In phase 2a (real integration through
`PlayerService`, see the next section), I validated the **actual visual content**
of the frames for the first time by extracting images from the recorded `.mkv`
outside WebRTC/the browser. They are **100% black**, with only RetroArch's own
notification overlays visible on top. "Bytes arriving" and "shaders compiling"
prove the capture pipeline works, but they do not prove that the game is being
drawn. This validation gap was missed on 2026-07-29 because no frame was
visually inspected at the time.

**Probable cause, partially confirmed but not fully resolved:** PCSX2's
"Hardware" renderer (OpenGL) is known to fail silently (black screen, no error)
when running over Mesa `llvmpipe` (software OpenGL). This behavior is documented
in several PCSX2/RetroArch issues (see "Known Open Blocker" below). Xvfb does
not expose the real GPU to GLX; even with `--gpus all`, the GPU is only used by
the *video encoder* (NVENC via ffmpeg), never by RetroArch/PCSX2 OpenGL itself,
which still falls back to `llvmpipe`.

The documented fix is the core option `pcsx2_renderer = "Software"` (use
PCSX2's native CPU rasterizer, which is more mature than relying on the hardware
pipeline over llvmpipe). **I tested that fix and it still stayed black**; see
details in the blocker below. The final root cause was not identified in this
session.

## Original Isolated POC Result (2026-07-29): Bytes In FIFO, Not Visually Validated

Run through `compose.ps2-poc.yml`, with `pcsx2_libretro.so` from the official
buildbot nightly, a real SCPH BIOS, and the commercial ISO *Black (USA)*,
provided locally by the user outside the repository:

```text
=== RESULT ===
RetroArch exit code : 124 (expected timeout, the test time limit)
Bytes received by FIFO : 509
glxinfo renderer : OpenGL renderer string: llvmpipe (LLVM 20.1.2, 256 bits)

==> SUCCESS: the core produced video through GL/Xvfb into the FIFO.
```

The core recognized the game (`[GameDB] Serial: SLUS-21376`), detected the
native resolution (`SET_SYSTEM_AV_INFO: 640x448, FPS: 59.94`), compiled the PGS
renderer GLSL shaders, and wrote to the FIFO at `960x720`, all on `llvmpipe`
(software rendering, with no dedicated GPU passed to the container).
**No frame was visually inspected at this stage**; see the correction above.

**Two discoveries that remain valid** and have already been incorporated into
production (`RetroArchDriver`/`FFmpegStreamingProvider`):

1. **BIOS path:** `pcsx2_libretro` looks for BIOS files in
   `<system_directory>/pcsx2/bios/`, not directly at the root of
   `system_directory` like Beetle PSX (PS1). `_ensure_bios()` already handles
   this via `_BIOS_CONFIG`.
2. **`video_gpu_record = "true"` is required:** `pcsx2_libretro` is
   `RETRO_HW_RENDER` (uses GL to draw), unlike PS1/SNES, which are pure
   software. Without this option in `retroarch.cfg`, RetroArch logs
   `"Libretro core is hardware rendered. Must use post-shaded recording as
   well.."` and **never writes to the FIFO**, even with the correct
   `--recordconfig`. Already incorporated in `_VIDEO_PROFILES["ps2"]`.

## Real Integration (Phase 2a): What Was Implemented And Validated

Work done in `RetroArchDriver`, `PlayerService`, `FFmpegStreamingProvider`, and
the production `Dockerfile`/`docker-entrypoint.sh` (no longer the isolated POC
image):

- **Per-console video profile** (`_VIDEO_PROFILES` in `retroarch.py`): PS2 uses
  `video_driver=gl`/`video_context_driver=x11`/`video_gpu_record=true` through
  a per-process `--appendconfig`; PS1/SNES stay on `video_driver=null` with no
  behavior change.
- **Per-console BIOS handling** (`_BIOS_CONFIG`, generalizing the old
  `_ensure_ps1_bios`): supports a core-specific subdirectory and fixed
  filenames (PS1) or free-form names identified by content (PS2).
- **Conditional Xvfb** in `docker-entrypoint.sh`: starts only if a `ps2` core is
  configured in `cores.json`, with no cost for users who only use PS1/SNES.
- **Optional PS2 in the production image:** `ARG INCLUDE_PS2=false` in the
  `Dockerfile`. Normal builds do not grow; `--build-arg INCLUDE_PS2=true` adds
  Xvfb/Mesa/the PS2 core (~150MB).
- **Unstable PS2 FIFO, a new and important discovery:** `pcsx2_libretro`
  re-emits `SET_SYSTEM_AV_INFO` 2-3 times during BIOS boot, before the game
  really starts. Each time, RetroArch restarts its recording subsystem, but
  from the second restart onward it **silently abandons the FIFO** passed with
  `-r` and starts writing to a timestamped `.mkv` file in
  `recording_output_directory`. Reading the original FIFO for PS2 makes ffmpeg
  read the truncated stream from the first abandoned recording and die with
  `"Error opening input: End of file"`.
- Fix: `FFmpegStreamingProvider._wait_for_settled_recording()` polls the
  records directory until the "latest recording" stops changing identity for a
  few seconds. This avoids starting too early (treating an intermediate pause as
  the final file) or too late (the file accumulates detectable duration and
  ffmpeg treats it as finished VOD instead of a stream). Once the right file is
  found, an intermediate `tail -f` process performs blocking reads, like a real
  FIFO, and feeds ffmpeg through `stdin`/`pipe:0`. Regular files do not block
  waiting for more bytes like a FIFO does, so ffmpeg reading the file directly
  treats "no bytes right now" as final EOF.
- `RetroArchDriver` keeps a disposable `cat` process reading the original FIFO,
  which nobody else consumes, only so RetroArch's `open()` does not block
  waiting for a reader that never arrives.
- **Longer streaming timeout for PS2**
  (`_STREAM_READY_TIMEOUT_OVERRIDES_SECONDS` in `player.py`): BIOS boot plus
  recording settle take much longer than the timeout tuned for PS1/SNES.
- **NVENC profile without `-profile:v baseline`:** pre-existing bug, not
  introduced by this work, discovered when testing NVENC for real in this
  project for the first time. Without forcing baseline, the browser does not
  reliably decode the NVIDIA driver's H.264 `Main` profile, resulting in audio
  working while video never appears. Fixed in `encoder_profiles.py`.
- **Browser-blocked video autoplay:** pre-existing frontend bug. `<video
  autoplay>` without `muted` can be silently blocked by browser policy, unlike
  `<audio autoplay>`, which browsers handle more permissively. Fixed by calling
  `.play()` explicitly with `.catch()`, matching the existing audio pattern in
  `app.js`.
- **Validated end to end successfully:** real BIOS, commercial ISO (*Black
  (USA)*), production container (`INCLUDE_PS2=true`), NVIDIA GPU passed through
  (`--gpus all`, encoder auto-detected as `h264_nvenc`), real `POST /play` API
  returning 200, session continuously publishing to mediamtx for 73s+ without
  error, and WebRTC delivering **real audio** in the browser.

## Known Open Blocker: Black Screen (GS Content Does Not Render)

**Symptom:** audio works, WebRTC connects, and RetroArch records continuously
for minutes without error, but every frame extracted from the video (recorded
`.mkv`, `.png` via `ffmpeg -frames:v 1`, inspected outside the browser/WebRTC)
is 100% black. RetroArch's own text notification overlays appear normally on
top of that black background, so the video composition/capture pipeline works;
the game's framebuffer (PS2 GS) never contains content.

**Ruled out as causes:**

- Wrong BIOS/wrong region (`scph39001.bin`, US, confirmed without load errors)
- SDL virtual joystick `LD_PRELOAD` (reproduced without it, same result)
- `input_driver` (`udev` was tested and crashes in the container because there
  is no real udevd; `sdl2`, the only viable option in the container, is not the
  cause)
- Screen camera/geometry (RetroArch correctly detects `640x448`/`960x720` with
  no error)
- `pcsx2_renderer = "Auto"` vs `"Software"` explicitly forced through
  `<config_dir>/config/LRPS2/LRPS2.opt` (made read-only to prevent RetroArch
  from resetting the option on exit, which was observed). **Tested and did not
  resolve it**; the log still shows `"[GameDB] Enabled GS Hardware Fix: ..."`
  being applied even with the renderer forced to software, suggesting the
  automatic GameDB patches for the specific game (*Black*) or some other config
  layer are ignoring the option.

**Research (see the full summary in the session history)** confirms that this
pattern, PCSX2 Hardware renderer black screen over Mesa `llvmpipe`, is known and
documented:
[PCSX2/pcsx2#455](https://github.com/pcsx2/pcsx2/issues/455),
[PCSX2/pcsx2#1813](https://github.com/PCSX2/pcsx2/issues/1813),
[libretro/RetroArch#14015](https://github.com/libretro/RetroArch/issues/14015),
[libretro forum](https://forums.libretro.com/t/pcsx2-katamari-damacy-and-we-love-katamari-black-screen-forever/35774).
The usual fix (`pcsx2_renderer=Software`) did not resolve this specific case.
There may be a second unidentified variable, such as GameDB patches for this
game, a renderer option that must be set through a mechanism other than direct
`--appendconfig`/`.opt`, or a deeper EGL/GLX+Xvfb+llvmpipe limitation not
covered by the researched issues.

**Untested hypothesis, next investigation line:** EGL surfaceless instead of
GLX+Xvfb. This is a more direct GL context path without an X server, potentially
avoiding the layers that cause the "not implemented" behavior observed in some
core `GET_VARIABLE` calls.

## Operational Note: PS2 Encoding Saturates CPU/Docker Desktop On This Machine

Several times during this session, running PS2 (even only encoding, or the
PCSX2 Software renderer) saturated CPU enough to **freeze all of Docker
Desktop**, not just the container, on this Windows/WSL2 development machine.
Recovery required killing `com.docker.*` processes and restarting WSL2 with
`wsl --shutdown`. This reinforces the user's original direction: PS2 is only
viable on machines with compatible hardware (real GPU available to the
container, enough CPU headroom). It must never become RetroHost's default
experience, and production docs/UX should make that explicit before any PR.

## Next Steps

1. **Resolve the black-screen blocker** before considering PS2 ready for any
   real use. Without that, the game runs but is never visible. Next line: EGL
   surfaceless, or test whether another game/homebrew without the *Black*
   GameDB-specific patches renders correctly. That would isolate whether the
   problem is systemic to the core+environment or specific to the tested title.
2. Measure performance on already supported "old retro" hardware (Pi) to
   confirm that changing `video_driver=null` to `gl` **does not regress**
   PS1/SNES, which is required by the "old retro first" direction. Not tested in
   this session.
3. Evaluate analog input (phase 2b, outside this phase's scope even after the
   black screen is resolved).
4. Only then consider opening the POC PR. The user's criterion is "minimally
   functional" with a real browser test, which has not been reached yet (audio
   works, video does not).
