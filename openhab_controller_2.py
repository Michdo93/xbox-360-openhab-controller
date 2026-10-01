import math
import asyncio
import pygame
from openhab import AsyncOpenHABClient
from openhab import AsyncItems

# ======================================================================
#  CONTROLLER-AUSWAHL
#  Setze CONTROLLER auf "xbox", "ps5_hid" oder "ps5_ds4"
# ======================================================================

CONTROLLER = "xbox"   # ← hier anpassen

# ======================================================================
#  Button- und Achsen-Mappings je Controller
# ======================================================================

MAPPINGS = {
    # ------------------------------------------------------------------
    "xbox": {
        # Buttons
        "A":            0,   # A
        "B":            1,   # B
        "X":            2,   # X
        "Y":            3,   # Y
        "LEFT_BUMPER":  4,   # LB
        "RIGHT_BUMPER": 5,   # RB
        "BACK":         6,   # Back
        "START":        7,   # Start
        # Achsen
        "LEFT_STICK_X":  0,
        "LEFT_STICK_Y":  1,
        "RIGHT_STICK_X": 2,
        "RIGHT_STICK_Y": 3,
        "LT_AXIS":       4,
        "RT_AXIS":       5,
        # Trigger-Schwellwert (Xbox: 0..1, PS5 analog: -1..1)
        "TRIGGER_THRESHOLD": 0.5,
    },

    # ------------------------------------------------------------------
    # PS5 DualSense – Treiber: hid-playstation (offizieller Linux-Kernel-Treiber)
    # Verfügbar ab Kernel 5.12. Empfohlen, da nativ und ohne Daemon.
    # Einrichten: kein Setup nötig, Kernel erkennt den Controller automatisch.
    # Achsen: L2/R2 laufen als eigene Achsen 4 und 5 (Wertebereich 0..1).
    "ps5_hid": {
        # Buttons
        "A":            0,   # Kreuz ✕
        "B":            1,   # Kreis ○
        "X":            2,   # Quadrat □
        "Y":            3,   # Dreieck △
        "LEFT_BUMPER":  4,   # L1
        "RIGHT_BUMPER": 5,   # R1
        "BACK":         8,   # Create
        "START":        9,   # Options
        # Achsen
        "LEFT_STICK_X":  0,
        "LEFT_STICK_Y":  1,
        "RIGHT_STICK_X": 2,
        "RIGHT_STICK_Y": 3,
        "LT_AXIS":       4,  # L2 (0..1)
        "RT_AXIS":       5,  # R2 (0..1)
        "TRIGGER_THRESHOLD": 0.5,
    },

    # ------------------------------------------------------------------
    # PS5 DualSense – Treiber: ds4drv (Userspace-Daemon für PS4/PS5)
    # Einrichten: pip install ds4drv  →  sudo ds4drv
    # Unter ds4drv werden L2/R2 auf Achse 3 und 4 gemappt.
    # ACHTUNG: Achse 3 ist gleichzeitig RIGHT_STICK_Y – ds4drv löst diesen
    # Konflikt intern auf; pygame sieht L2 auf Achse 3 und R2 auf Achse 4,
    # während RIGHT_STICK_Y auf Achse 5 wandert.
    "ps5_ds4": {
        # Buttons (identisch zu hid-playstation)
        "A":            0,   # Kreuz ✕
        "B":            1,   # Kreis ○
        "X":            2,   # Quadrat □
        "Y":            3,   # Dreieck △
        "LEFT_BUMPER":  4,   # L1
        "RIGHT_BUMPER": 5,   # R1
        "BACK":         8,   # Create
        "START":        9,   # Options
        # Achsen – ds4drv verschiebt Right Stick Y auf 5
        "LEFT_STICK_X":  0,
        "LEFT_STICK_Y":  1,
        "RIGHT_STICK_X": 2,
        "RIGHT_STICK_Y": 5,  # ← verschoben
        "LT_AXIS":       3,  # L2 (-1..1 unter ds4drv)
        "RT_AXIS":       4,  # R2 (-1..1 unter ds4drv)
        # ds4drv liefert Trigger im Bereich -1..1, Ruhewert ist -1
        "TRIGGER_THRESHOLD": 0.0,
    },
}

# Aktives Mapping laden
_M = MAPPINGS[CONTROLLER]

A_BUTTON      = _M["A"]
B_BUTTON      = _M["B"]
X_BUTTON      = _M["X"]
Y_BUTTON      = _M["Y"]
LEFT_BUMPER   = _M["LEFT_BUMPER"]
RIGHT_BUMPER  = _M["RIGHT_BUMPER"]
BACK_BUTTON   = _M["BACK"]
START_BUTTON  = _M["START"]

LEFT_STICK_X  = _M["LEFT_STICK_X"]
LEFT_STICK_Y  = _M["LEFT_STICK_Y"]
RIGHT_STICK_X = _M["RIGHT_STICK_X"]
RIGHT_STICK_Y = _M["RIGHT_STICK_Y"]
LT_AXIS       = _M["LT_AXIS"]
RT_AXIS       = _M["RT_AXIS"]
TRIGGER_THRESHOLD = _M["TRIGGER_THRESHOLD"]

# D-Pad (hat-basiert – bei allen drei Controllern identisch)
DPAD_UP    = (0,  1)
DPAD_DOWN  = (0, -1)
DPAD_LEFT  = (-1, 0)
DPAD_RIGHT = ( 1, 0)

# ======================================================================
#  OpenHAB-Konfiguration
# ======================================================================

URL      = "http://192.168.0.5:8080"
PASSWORD = ""
USERNAME = "openHABAdmin"

# ======================================================================
#  Tuning-Konstanten
# ======================================================================

HUE_STEP      = 5    # Farbauflösung in Grad (72 Stufen im Kreis)
HUE_MIN_DELTA = HUE_STEP
DEBOUNCE_MS   = 300  # Sperrzeit für wiederholbare Commands in ms

# ======================================================================
#  Command-Typen
# ======================================================================

class SimpleCommand:
    def __init__(self, item: str, value: str):
        self.item  = item
        self.value = value

class ToggleCommand:
    def __init__(self, item: str):
        self.item = item

# ======================================================================
#  OpenHABController
# ======================================================================

class OpenHABController:
    def __init__(self, items_api: AsyncItems, joystick):
        self.items    = items_api
        self.joystick = joystick

        self.rooms    = ["iKonferenz", "iKueche", "iBad", "iIoT", "iMultimedia"]
        self.stations = ["SWR3", "bigFM_BW", "Energy_Stuttgart",
                         "Radio_Regenbogen", "Antenne1", "DASDING"]

        self.volume_items = {
            "iKonferenz":  "iKonferenz_Sonos_Playbar_Lautstaerke",
            "iKueche":     "iKueche_Sonos_Lautsprecher_Lautstaerke",
            "iBad":        "iBad_Sonos_Lautsprecher_Lautstaerke",
            "iIoT":        "iIoT_Sonos_Lautsprecher_Lautstaerke",
            "iMultimedia": "iMultimedia_Sonos_Lautsprecher_Lautstaerke",
        }

        self.room_index    = 0
        self.station_index = 0

        self._lt_triggered = False
        self._rt_triggered = False

        self._pending_hue:   int | None = None
        self._hue_event      = asyncio.Event()
        self._last_sent_hue: int | None = None
        self._hue_busy       = False

        self._last_sent_ms: dict[str, float] = {}
        self.cmd_queue: asyncio.Queue = asyncio.Queue()

        print(f"🎮 Controller-Modus: {CONTROLLER}")
        print(f"🔊 Raum: {self.rooms[self.room_index]} | Sender: {self.stations[self.station_index]}")

    # ------------------------------------------------------------------ #
    #  Quantisierung                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _quantize_hue(raw: float) -> int:
        return round(raw / HUE_STEP) * HUE_STEP % 360

    # ------------------------------------------------------------------ #
    #  Queue-Hilfsmethoden (non-blocking)                                #
    # ------------------------------------------------------------------ #

    def send(self, item: str, value: str, debounce: bool = False):
        if debounce:
            now = pygame.time.get_ticks()
            key = f"{item}:{value}"
            if now - self._last_sent_ms.get(key, 0) < DEBOUNCE_MS:
                return
            self._last_sent_ms[key] = now
        self.cmd_queue.put_nowait(SimpleCommand(item, value))

    def toggle(self, item: str):
        self.cmd_queue.put_nowait(ToggleCommand(item))

    def _schedule_hue(self, x: float, y: float):
        x, y = -x, -y
        if abs(x) < 0.15 and abs(y) < 0.15:
            return

        raw_hue = (math.degrees(math.atan2(y, x)) + 360 - 90) % 360
        hue     = self._quantize_hue(raw_hue)

        if self._last_sent_hue is not None:
            delta = abs(hue - self._last_sent_hue)
            delta = min(delta, 360 - delta)
            if delta < HUE_MIN_DELTA:
                return

        self._pending_hue = hue
        if not self._hue_busy:
            self._hue_event.set()

    # ------------------------------------------------------------------ #
    #  Radio-Navigation                                                   #
    # ------------------------------------------------------------------ #

    def _current_radio_item(self) -> str:
        return f"{self.rooms[self.room_index]}_Webradio_{self.stations[self.station_index]}"

    def next_room(self):
        self.send(self._current_radio_item(), "OFF")
        self.room_index = (self.room_index + 1) % len(self.rooms)
        print(f"🏠 Raum → {self.rooms[self.room_index]}")
        self.send(self._current_radio_item(), "ON")

    def prev_room(self):
        self.send(self._current_radio_item(), "OFF")
        self.room_index = (self.room_index - 1) % len(self.rooms)
        print(f"🏠 Raum → {self.rooms[self.room_index]}")
        self.send(self._current_radio_item(), "ON")

    def next_station(self):
        self.send(self._current_radio_item(), "OFF")
        self.station_index = (self.station_index + 1) % len(self.stations)
        print(f"📻 Sender → {self.stations[self.station_index]}")
        self.send(self._current_radio_item(), "ON")

    def prev_station(self):
        self.send(self._current_radio_item(), "OFF")
        self.station_index = (self.station_index - 1) % len(self.stations)
        print(f"📻 Sender → {self.stations[self.station_index]}")
        self.send(self._current_radio_item(), "ON")

    def adjust_volume(self, direction: str):
        room = self.rooms[self.room_index]
        item = self.volume_items.get(room)
        if item:
            print(f"🔊 {direction} → {item}")
            self.send(item, direction, debounce=True)

    # ------------------------------------------------------------------ #
    #  Event-Handler (synchron – kein await!)                            #
    # ------------------------------------------------------------------ #

    def _on_axis(self, event):
        axis  = event.axis
        value = event.value

        if axis == LEFT_STICK_X:
            if value < -0.5:
                self.send("iSmartHome_Jalousie_Steuerung", "DOWN", debounce=True)
            elif value > 0.5:
                self.send("iSmartHome_Jalousie_Steuerung", "UP",   debounce=True)
            else:
                self.send("iSmartHome_Jalousie_Steuerung", "STOP", debounce=True)

        elif axis == LEFT_STICK_Y:
            if value < -0.5:
                self.send("iSmartHome_Somfy_Rollladen_Steuerung", "UP",   debounce=True)
            elif value > 0.5:
                self.send("iSmartHome_Somfy_Rollladen_Steuerung", "DOWN", debounce=True)
            else:
                self.send("iSmartHome_Somfy_Rollladen_Steuerung", "STOP", debounce=True)

        elif axis in (RIGHT_STICK_X, RIGHT_STICK_Y):
            rx = self.joystick.get_axis(RIGHT_STICK_X)
            ry = self.joystick.get_axis(RIGHT_STICK_Y)
            self._schedule_hue(rx, ry)

        elif axis == LT_AXIS:
            if value > TRIGGER_THRESHOLD and not self._lt_triggered:
                self._lt_triggered = True
                print("🎚 LT/L2 → Vorheriger Sender")
                self.prev_station()
            elif value <= TRIGGER_THRESHOLD:
                self._lt_triggered = False

        elif axis == RT_AXIS:
            if value > TRIGGER_THRESHOLD and not self._rt_triggered:
                self._rt_triggered = True
                print("🎚 RT/R2 → Nächster Sender")
                self.next_station()
            elif value <= TRIGGER_THRESHOLD:
                self._rt_triggered = False

    def _on_button(self, event):
        btn = event.button

        if btn == A_BUTTON:
            self.toggle(self._current_radio_item())
        elif btn == B_BUTTON:
            print("❄️  B/○ → Farbtemperatur DECREASE")
            self.send("iSmartHome_Hue_Lampen_Farbtemperatur", "DECREASE", debounce=True)
        elif btn == X_BUTTON:
            print("💡 X/□ → Hue-Lampe toggle")
            self.toggle("iSmartHome_Hue_Lampen_Schalter")
        elif btn == Y_BUTTON:
            print("🔥 Y/△ → Farbtemperatur INCREASE")
            self.send("iSmartHome_Hue_Lampen_Farbtemperatur", "INCREASE", debounce=True)
        elif btn == LEFT_BUMPER:
            print("⬅️  LB/L1 → Vorheriger Raum")
            self.prev_room()
        elif btn == RIGHT_BUMPER:
            print("➡️  RB/R1 → Nächster Raum")
            self.next_room()
        elif btn == BACK_BUTTON:
            print("⬅️  Back/Create → Morgenroutine Ausgangszustand toggle")
            self.toggle("iApplikation_Morgenroutine_Ausgangszustand")
        elif btn == START_BUTTON:
            print("▶️  Start/Options → Morgenroutine Start toggle")
            self.toggle("iApplikation_Morgenroutine_Start")

    def _on_dpad(self, event):
        dpad = self.joystick.get_hat(0)

        if dpad == DPAD_UP:
            self.adjust_volume("INCREASE")
        elif dpad == DPAD_DOWN:
            self.adjust_volume("DECREASE")
        elif dpad == DPAD_LEFT:
            print("💡⬅️  D-Pad LEFT → Helligkeit DECREASE")
            self.send("iSmartHome_Hue_Lampen_Helligkeit", "DECREASE", debounce=True)
        elif dpad == DPAD_RIGHT:
            print("💡➡️  D-Pad RIGHT → Helligkeit INCREASE")
            self.send("iSmartHome_Hue_Lampen_Helligkeit", "INCREASE", debounce=True)

    # ------------------------------------------------------------------ #
    #  Worker-Tasks                                                       #
    # ------------------------------------------------------------------ #

    async def _cmd_worker(self):
        while True:
            cmd = await self.cmd_queue.get()
            try:
                if isinstance(cmd, SimpleCommand):
                    await self.items.sendCommand(cmd.item, cmd.value)
                elif isinstance(cmd, ToggleCommand):
                    state = await self.items.getItemState(cmd.item)
                    value = "OFF" if "ON" in state else "ON"
                    print(f"🔀 Toggle {cmd.item} → {value}")
                    await self.items.sendCommand(cmd.item, value)
            except Exception as e:
                print(f"⚠️  Command-Fehler: {e}")
            finally:
                self.cmd_queue.task_done()

    async def _hue_worker(self):
        while True:
            await self._hue_event.wait()
            self._hue_event.clear()

            while True:
                hue = self._pending_hue
                if hue is None:
                    break

                self._hue_busy = True
                command = f"{hue},100,70"
                print(f"🎨 Hue={hue}° → {command}")
                try:
                    await self.items.sendCommand("iSmartHome_Hue_Lampen_Farbe", command)
                    self._last_sent_hue = hue
                except Exception as e:
                    print(f"⚠️  Hue-Fehler: {e}")

                if self._pending_hue != hue:
                    continue
                else:
                    break

            self._hue_busy = False

    # ------------------------------------------------------------------ #
    #  Game-Loop                                                          #
    # ------------------------------------------------------------------ #

    async def run_loop(self):
        print("🎮 Controller-Loop gestartet …")

        tasks = [
            asyncio.create_task(self._cmd_worker()),
            asyncio.create_task(self._hue_worker()),
        ]

        try:
            while True:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return
                    elif event.type == pygame.JOYAXISMOTION:
                        self._on_axis(event)
                    elif event.type == pygame.JOYBUTTONDOWN:
                        self._on_button(event)
                    elif event.type == pygame.JOYHATMOTION:
                        self._on_dpad(event)

                await asyncio.sleep(0.01)
        finally:
            for t in tasks:
                t.cancel()


# ======================================================================
#  Einstiegspunkt
# ======================================================================

async def main():
    if CONTROLLER not in MAPPINGS:
        print(f"❌ Unbekannter Controller: '{CONTROLLER}'")
        print(f"   Gültige Werte: {list(MAPPINGS.keys())}")
        return

    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("❌ Kein Controller gefunden!")
        return

    joy = pygame.joystick.Joystick(0)
    joy.init()

    async with AsyncOpenHABClient(url=URL, username=USERNAME, password=PASSWORD) as client:
        items_api = AsyncItems(client)
        controller = OpenHABController(items_api, joy)
        try:
            await controller.run_loop()
        except asyncio.CancelledError:
            pass
        finally:
            pygame.quit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
