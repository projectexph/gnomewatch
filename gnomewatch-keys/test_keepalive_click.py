"""The anti-idle click must land in the exact middle of the Roblox window, however the pointer was parked before. Uses a pretend Windows
(no real mouse is touched): Roblox is modelled the way it behaves - it only learns where the pointer is from real mouse-move events.
    python test_keepalive_click.py"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = ["x"]
os.environ.pop("GW_BOOT", None)
import sentinel as S

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class Fn:
    def __init__(self, f):
        self.f = f
        self.argtypes = self.restype = None

    def __call__(self, *a):
        return self.f(*a)


class World:
    """One window with its client area at (cx, cy) of size (w, h). The pointer starts somewhere else inside it."""
    def __init__(self, ignore_setcursor=False, drift=False):
        self.hwnd, self.cx, self.cy, self.w, self.h = 0xABC, 100, 200, 800, 600
        self.cursor = [160, 260]
        self.game_pos = list(self.cursor)         # where the GAME believes the pointer is: only real move events change it
        self.fg = 0xABC
        self.clicks, self.events = [], []
        self.ignore_setcursor, self.drift = ignore_setcursor, drift

    def set_cursor(self, x, y):
        if not self.ignore_setcursor:
            self.cursor[:] = [x, y]
        return 1

    def mouse_event(self, flags, dx, dy, data, extra):
        if flags & 0x0001:                         # relative move
            self.cursor[0] += dx
            self.cursor[1] += dy
            self.game_pos[:] = self.cursor
            self.events.append(("move", dx, dy))
        elif flags & 0x0002:
            self.clicks.append(tuple(self.game_pos))      # a click lands where the game thinks the pointer is
            self.events.append(("down",))
        elif flags & 0x0004:
            self.events.append(("up",))

    def user32(self):
        w = self
        P = ctypes.cast

        def get_cursor(ref):
            pt = ref._obj
            pt.x, pt.y = w.cursor
            return 1

        def get_client(h, ref):
            rc = ref._obj
            rc.left, rc.top, rc.right, rc.bottom = 0, 0, w.w, w.h
            return 1

        def client_to_screen(h, ref):
            pt = ref._obj
            pt.x += w.cx
            pt.y += w.cy
            return 1

        class U:
            WindowFromPoint = Fn(lambda pt: w.hwnd)
            GetAncestor = Fn(lambda h, f: h)
            GetForegroundWindow = Fn(lambda: w.fg)
            SetForegroundWindow = Fn(lambda h: 1)
            keybd_event = Fn(lambda *a: None)
            GetCursorPos = Fn(get_cursor)
            SetCursorPos = Fn(w.set_cursor)
            GetClientRect = Fn(get_client)
            ClientToScreen = Fn(client_to_screen)
            mouse_event = Fn(w.mouse_event)
        return U()


class FakeWin:
    def __init__(self, world):
        self.user32 = world.user32()


real_windll = ctypes.windll
S._mark_own = lambda: None
S.time.sleep = lambda s: None            # no waiting in the pretend world
try:
    w = World()
    ctypes.windll = FakeWin(w)
    ok = S._click_center(w.hwnd)
    centre = (w.cx + w.w // 2, w.cy + w.h // 2)
    check("it clicks", ok is True)
    check("exactly one click", len(w.clicks) == 1, w.clicks)
    check("the click is in the exact middle of the window (+/- 2 px), not where the pointer was parked",
          w.clicks and abs(w.clicks[0][0] - centre[0]) <= 2 and abs(w.clicks[0][1] - centre[1]) <= 2, (w.clicks, centre))
    check("the pointer is put back where it was", w.cursor == [160, 260], w.cursor)
    check("the game was shown the pointer arriving (real move events) before the click", ("move", 1, 1) in w.events and w.events.index(("down",)) > w.events.index(("move", 1, 1)), w.events)

    # a window in another place and size
    w = World()
    w.cx, w.cy, w.w, w.h = 1930, 40, 1280, 720
    w.cursor[:] = [1940, 50]
    w.game_pos[:] = w.cursor
    ctypes.windll = FakeWin(w)
    S._click_center(w.hwnd)
    centre = (w.cx + w.w // 2, w.cy + w.h // 2)
    check("same on a second monitor / other size", w.clicks and abs(w.clicks[0][0] - centre[0]) <= 2 and abs(w.clicks[0][1] - centre[1]) <= 2, (w.clicks, centre))

    # the pointer can't be placed: it must not click at all
    w = World(ignore_setcursor=True)
    ctypes.windll = FakeWin(w)
    ok = S._click_center(w.hwnd)
    check("if the pointer can't be put on the spot, it does not click blind", ok is False and not w.clicks, (ok, w.clicks))

    # the window isn't in front: no click
    w = World()
    w.fg = 0x999
    ctypes.windll = FakeWin(w)
    ok = S._click_center(w.hwnd)
    check("not in front: no click", ok is False and not w.clicks, (ok, w.clicks))

    # the window moves while we're aiming: no click
    w = World()
    orig = w.mouse_event

    def moving(flags, dx, dy, data, extra):
        orig(flags, dx, dy, data, extra)
        if flags & 0x0001 and dx == -1:
            w.cx += 40                              # the window slides away right after the pointer got there
    w.mouse_event = moving
    ctypes.windll = FakeWin(w)
    ok = S._click_center(w.hwnd)
    check("window moved while aiming: no click", ok is False and not w.clicks, (ok, w.clicks))
finally:
    ctypes.windll = real_windll
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
