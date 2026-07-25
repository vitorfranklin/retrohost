"""Map: logical key name -> evdev/uinput keycode, and Gamepad API button index
("standard" mapping) -> logical name.

The logical names ("a", "b", "up", "start"...) correspond to the current
input_player1_* binds in retroarch.cfg (a="x", b="z", up="up", start="enter",
etc — confirmed in /home/YOUR_USER/.config/retroarch/retroarch.cfg on the real Pi).
They are also the vocabulary used in WebSocket messages from the frontend.

The keycodes below are integer constants hardcoded (not `import evdev`)
so this module remains importable in any environment (Windows dev,
tests) without python3-evdev installed — only uinput_keyboard.py
(the only place that actually talks to the kernel) depends on evdev being present.
Values match linux/input-event-codes.h.
"""

EVDEV_KEYCODES: dict[str, int] = {
    "a": 45,  # KEY_X
    "b": 44,  # KEY_Z
    "x": 31,  # KEY_S
    "y": 30,  # KEY_A
    "l": 16,  # KEY_Q
    "r": 17,  # KEY_W
    "select": 54,  # KEY_RIGHTSHIFT
    "start": 28,  # KEY_ENTER
    "up": 103,  # KEY_UP
    "down": 108,  # KEY_DOWN
    "left": 105,  # KEY_LEFT
    "right": 106,  # KEY_RIGHT
}

# Gamepad API button index ("standard" mapping) -> logical name above.
#
# Note: button 0 (physical "A" position on an Xbox-style pad) maps to "b",
# and button 1 (physical "B") maps to "a" — convention used by libretro's own
# autoconfig profiles for Xbox-style pads. Not yet validated with a real physical
# controller; if buttons come out swapped in real testing, just swap this pair
# here (no architectural changes needed).
#
# Indices without an entry (6, 7 = L2/R2; 10, 11 = L3/R3) are intentionally
# omitted — "nul" (no bind) in RetroArch today, no effect.
GAMEPAD_BUTTON_TO_KEY: dict[int, str] = {
    0: "b",
    1: "a",
    2: "y",
    3: "x",
    4: "l",
    5: "r",
    8: "select",
    9: "start",
    12: "up",
    13: "down",
    14: "left",
    15: "right",
}
