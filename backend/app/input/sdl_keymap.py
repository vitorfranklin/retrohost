"""Map: logical key name -> button index in the virtual SDL joystick (see
container/sdl_input_preload.c). Used by SDLGamepadProvider in the container.

The virtual joystick is recognized by SDL as an SDL_GameController with this
mapping (confirmed via SDL_GameControllerMappingForDeviceIndex):
  a:b0 b:b1 x:b2 y:b3 back:b4 guide:b5 start:b6 leftstick:b7 rightstick:b8
  leftshoulder:b9 rightshoulder:b10 dpup:b11 dpdown:b12 dpleft:b13 dpright:b14
IMPORTANT: the d-pad is exposed as BUTTONS (b11-b14), not as a hat — so
all entries use button indices.

The logical names ("a","b","up"...) are the frontend/WebSocket vocabulary,
same as in keymap.py (uinput path). The logical RetroPad maps as:
RetroPad B = SDL A(b0), RetroPad A = SDL B(b1) — convention from autoconfigs
for Xbox-style pads; adjustable here if swapped in real testing.
"""

# logical name -> SDL button index
SDL_BUTTON_MAP: dict[str, int] = {
    "b": 0,       # RetroPad B  -> SDL A (sul)
    "a": 1,       # RetroPad A  -> SDL B (leste)
    "y": 2,       # RetroPad Y  -> SDL X (oeste)
    "x": 3,       # RetroPad X  -> SDL Y (norte)
    "select": 4,  # back
    "start": 6,   # start
    "l": 9,       # leftshoulder
    "r": 10,      # rightshoulder
    "up": 11,     # dpup
    "down": 12,   # dpdown
    "left": 13,   # dpleft
    "right": 14,  # dpright
}
