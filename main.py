"""Device Battery: battery levels of Bluetooth earbuds and headsets, BLE
devices and Xbox/XInput controllers (see sources.py for where each comes
from), plus left bud, right bud, case and Find for Fast Pair earbuds (see
fastpair.py; needs a 2 MB one-time download). The tile shows the lowest
level, the page shows everything, and anything under 20% turns red and puts
a warning glyph on the collapsed pill.

Bluetooth properties are re-read every 30 s and the slower BLE GATT read
every 5 minutes (and whenever the page opens), on background threads so the
tile never waits. Fast Pair earbuds push their own updates."""

import json
import math
import sys
import threading
import time

import nook
import deps

app = nook.Plugin()
deps.setup(app.data_dir)

import fastpair  # noqa: E402
import setup_flow  # noqa: E402

if sys.platform == "win32":
    import sources
else:  # lets the checker run elsewhere
    sources = None

THRESHOLDS = (10, 20, 30)
CARD_PAD = 14
KIND_ICON = {"earbuds": "headphones", "headphones": "headphones", "controller": "game-controller",
             "mouse": "mouse", "keyboard": "keyboard", "pen": "pen", "other": "bluetooth"}
RING_WORDS = {1: "right bud", 2: "left bud", 3: "both buds"}

settings = {"show_disconnected": False, "popup": False, "low": 20}
settings.update(app.load("settings", {}))
readings = {"pnp": [], "xinput": [], "gatt": []}
ui = {"busy": False, "checked": None, "warned": None, "view": 0, "find": None, "page_w": 572}
last_sent = {"tile": None, "page": None, "badge": None}
buds = {"fp": None}          # the FastPair watcher, once its packages are installed


def post(msg):
    app._inbox.put(msg)


setup = setup_flow.Setup(
    app, "See each earbud and the case, and ring them to find",
    packages=fastpair.PACKAGES, packages_label="Windows Bluetooth bindings (winrt)",
    on_change=lambda: start_fastpair() or draw())


def start_fastpair():
    if setup.ready and buds["fp"] is None and sources:
        buds["fp"] = fastpair.FastPair(lambda: post({"type": "x-fastpair"}))
        buds["fp"].start()


@app.on_message("x-fastpair")
def on_fastpair(_m):
    draw()


# Gathering ------------------------------------------------------------------------

def read_fast():
    if not sources:
        return
    for name, fn in (("pnp", sources.pnp_batteries), ("xinput", sources.xinput_batteries)):
        try:
            readings[name] = fn()
        except OSError as e:
            print(f"{name}: {e}", file=sys.stderr)


def read_gatt():
    if not sources or ui["busy"]:
        return
    ui["busy"] = True

    def work():
        found = sources.gatt_batteries()
        post({"type": "gatt_done", "found": found})

    threading.Thread(target=work, daemon=True).start()


@app.on_message("gatt_done")
def on_gatt(msg):
    readings["gatt"] = msg["found"]
    ui["busy"] = False
    ui["checked"] = time.strftime("%H:%M")
    draw()


def earbud_devices():
    """Fast Pair earbuds as devices: "level" is the lower bud, "buds" has
    the detail."""
    out = []
    for s in buds["fp"].snapshot() if buds["fp"] else []:
        levels = [b["level"] for b in (s["left"], s["right"]) if b["level"] is not None]
        out.append({"key": "fp:" + s["address"], "name": s["name"], "kind": "earbuds",
                    "level": min(levels) if levels else None, "approx": False,
                    "connected": s["connected"], "source": "Fast Pair", "buds": s})
    return out


def devices():
    """Everything, lowest first. Fast Pair detail replaces the single
    Bluetooth number for the same earbuds, and a GATT reading is dropped when
    the Bluetooth property already reports that device."""
    rich = earbud_devices()
    covered = {d["name"].lower() for d in rich}
    out = [d for d in readings["pnp"] if d["name"].lower() not in covered]
    names = covered | {d["name"].lower() for d in out}
    out += [d for d in readings["gatt"] if d["name"].lower() not in names]
    out += readings["xinput"] + rich
    if not settings["show_disconnected"]:
        out = [d for d in out if d["connected"]]
    return sorted(out, key=lambda d: (d["level"] is None, d["level"] if d["level"] is not None else 0))


def lowest(devs):
    with_level = [d for d in devs if d["level"] is not None and d["connected"]]
    return min(with_level, key=lambda d: d["level"]) if with_level else None


def is_low(d):
    return d["level"] is not None and d["level"] < settings["low"]


def level_text(d):
    if d["level"] is None:
        return "Wired" if "wired" in d["source"] else "—"
    return d.get("word", f"~{d['level']}%") if d["approx"] else f"{d['level']}%"


def color_of(level):
    if level is None:
        return "faint"
    return "red" if level < settings["low"] else ("orange" if level < 40 else "green")


def color_for(d):
    return color_of(d["level"])


def pct(b):
    return "—" if b["level"] is None else f"{b['level']}%"


def short(name, limit=22):
    return name if len(name) <= limit else name[:limit - 1].rstrip() + "…"


# Tile -------------------------------------------------------------------------------

def tile_tip(devs):
    lines = []
    for d in devs:
        if d.get("buds"):
            b = d["buds"]
            lines.append(f"{d['name']}: left {pct(b['left'])}, right {pct(b['right'])}, "
                         f"case {pct(b['case'])}")
        else:
            lines.append(f"{d['name']}: {level_text(d)}")
    return "\n".join(lines)


TRACK = "#8080802E"
SWEEP = 270          # degrees of a dial, open at the bottom


def arc_shapes(cx, cy, r, level, stroke):
    """A dial's track and, for a known level, its value arc with round caps."""
    shapes = [nook.arc(cx, cy, r, -SWEEP / 2, SWEEP / 2, color=TRACK, width=stroke)]
    if level:
        color = color_of(level)
        end = -SWEEP / 2 + SWEEP * level / 100
        shapes.append(nook.arc(cx, cy, r, -SWEEP / 2, end, color=color, width=stroke))
        for deg in (-SWEEP / 2, end):
            a = math.radians(deg - 90)
            shapes.append(nook.circle(cx + r * math.cos(a), cy + r * math.sin(a), stroke / 2,
                                      fill=color))
    return shapes


def mini_dial(cx, cy, r, level, caption=None):
    """A tile dial: the number inside, and a caption in the gap at the bottom."""
    stroke = max(3.0, r * 0.14)
    shapes = arc_shapes(cx, cy, r, level, stroke)
    shapes.append(nook.label(cx, cy, "—" if level is None else str(level),
                             size=max(11, r * 0.62), bold=True,
                             color="faint" if level is None else "text", anchor="center"))
    if caption:
        shapes.append(nook.label(cx, cy + r * 0.86, caption, size=max(9, r * 0.27),
                                 color="dim", anchor="center"))
    return shapes


def tile_view():
    """Drawn to the tile's exact size, so it's centred and fills the tile."""
    devs = devices()
    w, h = app.tile_width or 96, app.tile_height or 96
    size = app.tile_size or "small"
    tip = tile_tip(devs)
    ringing = next((d for d in devs if d.get("buds") and d["buds"]["ringing"]), None)
    pair = next((d for d in devs if d.get("buds") and d["connected"]), None)
    low = lowest(devs)
    if ringing:
        shapes = tile_message(w, h, "speaker-high", "Ringing", "accent")
        tip = f"Ringing {ringing['name']}. Open to stop."
    elif not low and not pair:
        shapes = tile_message(w, h, "bluetooth", "No devices", "faint")
        tip = tip or "No battery-powered Bluetooth devices or controllers connected"
    elif size == "wide":
        shapes = tile_wide(w, h, pair, low)
    elif size == "large":
        shapes = tile_large(w, h, pair, devs)
    else:
        shapes = tile_small(w, h, pair, low)
    return nook.canvas(h, shapes, width=w, tooltip=tip)


def tile_message(w, h, icon, text, color):
    big = h > 70
    shapes = [nook.glyph(w / 2, h / 2 - (10 if big else 0), icon, size=26 if big else 20,
                         color=color)]
    if big:
        shapes.append(nook.label(w / 2, h / 2 + 18, text, size=11, color="dim",
                                 anchor="center"))
    return shapes


def tile_small(w, h, pair, low):
    """One dial for the lowest level; earbuds also say L and R."""
    side = min(w, h)
    roomy = side > 110
    r = side * (0.34 if roomy else 0.4)
    cy = h * (0.44 if roomy else 0.5)
    if pair:
        b = pair["buds"]
        level = pair["level"]
        caption = (f"L{b['left']['level'] if b['left']['level'] is not None else '–'} "
                   f"R{b['right']['level'] if b['right']['level'] is not None else '–'}")
        name = pair["name"]
    else:
        level, caption, name = low["level"], "%", low["name"]
    shapes = mini_dial(w / 2, cy, r, level, caption if roomy else None)
    if roomy:
        shapes.append(nook.label(w / 2, h - side * 0.09, short(name, int(w / 7)),
                                 size=max(9, side * 0.07), color="dim", anchor="center"))
    return shapes


def tile_wide(w, h, pair, low):
    """Earbuds: L and R dials, the name and the case; else one dial and the
    lowest device."""
    r = h * 0.27
    cy = h * 0.5
    text_x = h * 1.5 if pair else h * 0.86
    if pair:
        b = pair["buds"]
        shapes = mini_dial(h * 0.42, cy, r, b["left"]["level"], "L")
        shapes += mini_dial(h * 1.06, cy, r, b["right"]["level"], "R")
        name = pair["name"]
        detail = (f"Case {b['case']['level']}%" if b["case"]["level"] is not None
                  else "Connected")
    else:
        shapes = mini_dial(h * 0.45, cy, r, low["level"])
        name, detail = low["name"], level_text(low)
    lines = two_lines(name, max(6, int((w - text_x - 8) / 6.5)))
    top = cy - 8 * len(lines)
    for i, line in enumerate(lines):
        shapes.append(nook.label(text_x, top + 16 * i, line, size=12, bold=True,
                                 anchor="start"))
    shapes.append(nook.label(text_x, top + 16 * len(lines) + 4, detail, size=11, color="dim",
                             anchor="start"))
    return shapes


def two_lines(name, chars):
    """`name` on one line, or split at a space onto two, shortened to fit."""
    if len(name) <= chars:
        return [name]
    cut = name.rfind(" ", 0, chars + 1)
    if cut <= 0:
        return [short(name, chars)]
    return [name[:cut], short(name[cut + 1:], chars)]


def tile_large(w, h, pair, devs):
    """Earbuds as Left / Right / Case dials, other devices as bars, the
    whole block centred in the tile."""
    others = [d for d in devs if d is not pair]
    r = min(w / 6 - 16, h * 0.15)
    dials_h = 30 + 2 * r + 18 if pair else 0
    rows = others[:max(0, int((h - 24 - dials_h) // 30))]
    y = max(12.0, (h - dials_h - 30 * len(rows)) / 2)
    shapes = []
    if pair:
        b = pair["buds"]
        shapes.append(nook.label(12, y + 4, short(pair["name"], int(w / 7)), size=12,
                                 bold=True, anchor="start"))
        cy = y + 28 + r
        for i, (part, word) in enumerate(((b["left"], "Left"), (b["right"], "Right"),
                                          (b["case"], "Case"))):
            shapes += mini_dial(w * (2 * i + 1) / 6, cy, r, part["level"], word)
        y += dials_h
    for d in rows:
        shapes += [nook.glyph(20, y + 6, KIND_ICON[d["kind"]], size=14, color=color_for(d)),
                   nook.label(34, y + 6, short(d["name"], int(w / 8)), size=11,
                              anchor="start"),
                   nook.label(w - 12, y + 6, level_text(d), size=11, bold=True,
                              color="red" if is_low(d) else "text", anchor="end"),
                   nook.rect(12, y + 16, w - 24, 4, fill=TRACK, radius=2)]
        if d["level"] is not None:
            shapes.append(nook.rect(12, y + 16, max(4, (w - 24) * d["level"] / 100), 4,
                                    fill=color_for(d), radius=2))
        y += 30
    return shapes


# Page -------------------------------------------------------------------------------

def dial(cx, cy, r, b, word, note, note_color, active, ringing):
    """Shapes for one dial: track, value arc with round caps, the number,
    and the name and a note underneath."""
    level = b["level"] if active else None
    shapes = arc_shapes(cx, cy, r, level, 7)
    if ringing:
        shapes.append(nook.circle(cx, cy, r + 7, stroke="accent", width=2))
    charging = b["charging"] and active
    shapes.append(nook.label(cx, cy - (5 if charging else 0),
                             f"{level}%" if level is not None else "—",
                             size=22 if level is not None else 20, bold=True,
                             color="text" if level is not None else "faint", anchor="center"))
    if charging:
        shapes.append(nook.glyph(cx, cy + 17, "lightning", size=13, color="green", fill=True))
    shapes.append(nook.label(cx, cy + r + 17, word, size=13, bold=True, anchor="center"))
    if note:
        shapes.append(nook.label(cx, cy + r + 34, note, size=11, color=note_color,
                                 anchor="center"))
    return shapes


def earbuds_visual(d, width):
    """Left, Right and Case as three evenly spaced dials, drawn on a canvas
    so they line up exactly."""
    b = d["buds"]
    active = b["active"] or {"left": True, "right": True}
    r, cy = 42, 54
    shapes = []
    parts = (("Left", b["left"], active["left"], 2), ("Right", b["right"], active["right"], 1),
             ("Case", b["case"], True, 0))
    for i, (word, part, on, bit) in enumerate(parts):
        ringing = bool(b["ringing"] & bit)
        if ringing:
            note, note_color = "Ringing", "accent"
        elif not on:
            note, note_color = "In the case", "faint"
        elif part["charging"]:
            note, note_color = "Charging", "green"
        elif part["level"] is None:
            note, note_color = ("Dock a bud to read" if word == "Case" else "No reading"), "faint"
        else:
            note, note_color = "", "faint"
        shapes += dial(width * (2 * i + 1) / 6, cy, r, part, word, note, note_color, on,
                       ringing)
    return nook.canvas(cy + r + 46, shapes, width=width)


def find_panel(d):
    b, a = d["buds"], d["buds"]["address"]
    if b["ringing"]:
        return [nook.text(f"Ringing the {RING_WORDS.get(b['ringing'], 'buds')}… it stops "
                          f"by itself after {fastpair.RING_LIMIT} s.", size="small",
                          color="accent"),
                nook.button("stop:" + a, label="Stop ringing", icon="speaker-slash",
                            primary=True, width=0)]
    if ui["find"] != a:
        return []
    active = b["active"] or {"left": True, "right": True}
    choices = []
    if active["left"]:
        choices.append(nook.button(f"ring:left:{a}", label="Left", icon="speaker-high", width=0))
    if active["left"] and active["right"]:
        choices.append(nook.button(f"ring:both:{a}", label="Both", icon="speaker-high",
                                   primary=True, width=0))
    if active["right"]:
        choices.append(nook.button(f"ring:right:{a}", label="Right", icon="speaker-high",
                                   width=0))
    if not choices:
        return [nook.text("Both buds are in the case; take one out to ring it.",
                          size="small", color="dim")]
    return [nook.text("Take them out of your ears first: they ring loudly.", size="small",
                      color="orange"),
            nook.grid(len(choices), choices, gap=6)]


def earbuds_card(d):
    b = d["buds"]
    sub = ["Connected" if d["connected"] else "Not connected · last known levels"]
    if b["firmware"]:
        sub.append(f"firmware {b['firmware']}")
    header = [nook.icon("headphones", size=24, color="accent"),
              nook.column([nook.text(d["name"], bold=True),
                           nook.text(" · ".join(sub), size="small", color="dim")], gap=1),
              nook.spacer()]
    if d["connected"] and not b["ringing"]:
        header.append(nook.button("find:" + b["address"], label="Find", icon="speaker-high",
                                  primary=ui["find"] == b["address"],
                                  tooltip="Ring the earbuds"))
    body = [nook.row(header, gap=10, align="center"),
            earbuds_visual(d, ui["page_w"] - 2 * CARD_PAD)]
    if d["connected"]:
        body += find_panel(d)
    if b["error"]:
        body.append(nook.text(b["error"], size="small", color="red", wrap=True))
    return nook.card(body, padding=CARD_PAD, gap=12)


def device_card(d):
    top = [nook.icon(KIND_ICON[d["kind"]], size=18, color=color_for(d)),
           nook.text(short(d["name"], 22), bold=True), nook.spacer(),
           nook.text(level_text(d), size="title", color="red" if is_low(d) else "text")]
    card = [nook.row(top, gap=6, align="center")]
    if d["level"] is not None:
        card.append(nook.progress(d["level"] / 100, color=color_for(d)))
    card.append(nook.text(d["source"] + ("" if d["connected"] else " · not connected"),
                          size="small", color="faint"))
    return nook.card(card, gap=6, padding=10)


def devices_page(devs):
    body = []
    lows = [d for d in devs if is_low(d)]
    if lows:
        body.append(nook.card([nook.row([
            nook.icon("battery-warning", color="red"),
            nook.text(", ".join(d["name"] for d in lows) + f" below {settings['low']}%",
                      color="red", bold=True, wrap=True)], gap=8, align="center")], padding=10))
    earbuds = [d for d in devs if d.get("buds")]
    others = [d for d in devs if not d.get("buds")]
    body += [earbuds_card(d) for d in earbuds]
    if others:
        body.append(nook.grid(2, [device_card(d) for d in others], gap=8))
    # Plain earbuds that Fast Pair could tell more about, once set up.
    if not setup.ready and any(d["kind"] == "earbuds" for d in others):
        body.append(setup.card())
    if not devs:
        body.append(nook.card([nook.column([
            nook.spacer(18),
            nook.icon("bluetooth-slash", size=30, color="faint"),
            nook.text("No battery-powered devices connected", bold=True),
            nook.text("Connect earbuds, a headset or a controller.", size="small",
                      color="dim"),
            nook.spacer(18)], gap=6, align="center")], padding=12))
    status = "Checking Bluetooth LE devices…" if ui["busy"] else (
        f"Bluetooth LE checked at {ui['checked']}" if ui["checked"] else "")
    if status:
        body.append(nook.text(status, size="small", color="faint"))
    return body


SOURCES = [
    ("headphones", "Fast Pair earbuds", "Each bud, the case and Find, over their message stream"),
    ("bluetooth", "Other earbuds and headsets", "One number, through the hands-free profile"),
    ("mouse", "Mice, keyboards, trackers", "The Bluetooth LE battery service, every 5 minutes"),
    ("game-controller", "Xbox controllers", "XInput: empty, low, medium or full"),
]


def section(title, children):
    return nook.card([nook.text(title, size="small", color="dim")] + children,
                     padding=12, gap=8)


def earbud_details_section():
    if not setup.ready:
        return setup.card()
    pairs = [d for d in earbud_devices() if d["connected"]]
    status = (f"Reading {', '.join(d['name'] for d in pairs)}" if pairs else
              "Connect Fast Pair earbuds to see each bud and the case")
    return section("Earbud details", [nook.row([
        nook.icon("check-circle", size=20, color="green", fill=True),
        nook.column([nook.text("Fast Pair is set up", bold=True),
                     nook.text(status, size="small", color="dim")], gap=1),
    ], gap=10, align="center")])


def settings_page():
    low = settings["low"] if settings["low"] in THRESHOLDS else 20
    return [
        earbud_details_section(),
        section("Low battery", [
            nook.row([nook.text("Warn below"), nook.spacer(),
                      nook.chips("low", [f"{t}%" for t in THRESHOLDS],
                                 selected=THRESHOLDS.index(low))], align="center"),
            nook.toggle("popup", settings["popup"], label="Open this page when it happens"),
        ]),
        section("Devices", [
            nook.toggle("show_disconnected", settings["show_disconnected"],
                        label="Show paired devices that aren't connected"),
        ]),
        section("Where the numbers come from", [
            nook.row([nook.icon(icon, size=18, color="dim"),
                      nook.column([nook.text(title),
                                   nook.text(note, size="small", color="faint")], gap=0)],
                     gap=10, align="center")
            for icon, title, note in SOURCES
        ]),
    ]


def page_view():
    devs = devices()
    body = [nook.chips("view", ["Devices", "Settings"], selected=ui["view"])]
    body += settings_page() if ui["view"] == 1 else devices_page(devs)
    return nook.column(body, gap=10, scroll=True)


ACTIONS = [{"id": "bt", "icon": "bluetooth", "tooltip": "Bluetooth settings"},
           {"id": "refresh", "icon": "arrow-clockwise", "tooltip": "Check again"}]


def badge():
    devs = devices()
    if any(d.get("buds") and d["buds"]["ringing"] for d in devs):
        return {"kind": "glyph", "icon": "speaker-high"}
    return {"kind": "glyph", "icon": "battery-warning"} if any(is_low(d) for d in devs) else None


def draw(force=False):
    for key, value, send in (("tile", tile_view(), app.tile),
                             ("page", page_view(), lambda v: app.page(v, ACTIONS)),
                             ("badge", badge(), lambda b: nook.emit({"type": "badge", "badge": b}))):
        k = json.dumps(value, sort_keys=True)
        if force or k != last_sent[key]:
            send(value)
            last_sent[key] = k
    # Open the page once each time a device crosses below the threshold.
    low_now = {d["key"] for d in devices() if is_low(d)}
    if settings["popup"] and ui["warned"] is not None and low_now - ui["warned"]:
        app.show()
    ui["warned"] = low_now


# Events -------------------------------------------------------------------------

@app.on_message("event")
def on_event(e):
    eid, value = e.get("id", ""), e.get("value")
    if setup.handles(eid):
        return
    if eid == "view" and isinstance(value, dict):
        ui["view"] = value.get("index", 0)
    elif eid == "low" and isinstance(value, dict):
        index = max(0, min(value.get("index", 1), len(THRESHOLDS) - 1))
        settings["low"] = THRESHOLDS[index]
        app.save("settings", settings)
    elif eid in ("show_disconnected", "popup"):
        settings[eid] = bool(value)
        app.save("settings", settings)
    elif eid == "refresh":
        read_fast()
        read_gatt()
        if buds["fp"]:
            buds["fp"].refresh()
    elif eid == "bt":
        app.open("ms-settings:bluetooth")
    elif eid.startswith("find:"):
        address = eid[5:]
        ui["find"] = None if ui["find"] == address else address
    elif eid.startswith("ring:") and buds["fp"]:
        _, which, address = eid.split(":", 2)
        buds["fp"].ring(address, which)
    elif eid.startswith("stop:") and buds["fp"]:
        buds["fp"].ring(eid[5:], "stop")
        ui["find"] = None
    draw()


@app.on_message("page_open")
def on_page_open(_):
    read_fast()
    read_gatt()
    if buds["fp"]:
        buds["fp"].refresh()
    draw()


@app.on_message("layout")
def on_layout(msg):
    if msg.get("target") == "page" and msg.get("width"):
        ui["page_w"] = msg["width"]
        draw()
    elif msg.get("target") == "tile":
        draw(force=True)


def tick_fast():
    read_fast()
    draw()


start_fastpair()
read_fast()
draw(force=True)
read_gatt()
app.every(30, tick_fast)
app.every(300, read_gatt)
app.run()
