import math
import asyncio
import pygame
import time
from openhab import AsyncOpenHABClient
from openhab import AsyncItems

# === KONFIGURATION ===
URL      = "http://192.168.0.5:8080"
PASSWORD = ""
USERNAME = "openHABAdmin"

# Button Mapping (XBox)
A_BUTTON     = 0
B_BUTTON     = 1
X_BUTTON     = 2
Y_BUTTON     = 3
LEFT_BUMPER  = 4
RIGHT_BUMPER = 5
BACK_BUTTON  = 6
START_BUTTON = 7
L_STICK_IN    = 8
R_STICK_IN    = 9

# Achsen
LEFT_STICK_X  = 0
LEFT_STICK_Y  = 1
RIGHT_STICK_X = 2
RIGHT_STICK_Y = 3
LT_AXIS       = 4
RT_AXIS       = 5

# D-Pad
DPAD_UP    = (0,  1)
DPAD_DOWN  = (0, -1)
DPAD_LEFT  = (-1, 0)
DPAD_RIGHT = ( 1, 0)

# ======================================================================
#  Tuning-Konstanten
# ======================================================================

# Hue: Auf wieviel Grad runden? (5 = 72 mögliche Farben im Kreis)
HUE_STEP = 5

# Hue: Minimale Gradänderung zum letzten gesendeten Wert, die einen
# neuen Command auslöst. Verhindert Flattern an Stufengrenzen.
HUE_MIN_DELTA = HUE_STEP

# Debounce für wiederholbare Commands (Jalousie, Rollladen, Lautstärke,
# Helligkeit, Farbtemperatur). Innerhalb dieses Fensters wird ein
# identischer Command für dasselbe Item nicht erneut gesendet.
# Sollte ca. der Gesamt-Latenz der Befehlskette entsprechen (~300 ms).
DEBOUNCE_MS = 300


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

        # LT/RT Trigger-Guards
        self._lt_triggered = False
        self._rt_triggered = False

        # ----------------------------------------------------------------
        #  Hue-State
        #
        #  _pending_hue  – neuester quantisierter Hue-Wert vom Stick
        #  _hue_event    – weckt den Worker, sobald ein neuer Wert anliegt
        #  _last_sent_hue – zuletzt tatsächlich gesendeter Wert
        #  _hue_busy     – True, solange der Worker einen HTTP-Call macht;
        #                  verhindert, dass neue Events die Queue füllen,
        #                  während der vorherige Befehl noch unterwegs ist
        # ----------------------------------------------------------------
        self._pending_hue:   int | None = None
        self._hue_event      = asyncio.Event()
        self._last_sent_hue: int | None = None
        self._hue_busy       = False

        # ----------------------------------------------------------------
        #  Debounce-Tabelle für wiederholbare Commands
        #  { item: letzter Sendezeitpunkt in ms }
        # ----------------------------------------------------------------
        self._last_sent_ms: dict[str, float] = {}

        # Normale Command-Queue (alle außer Hue)
        self.cmd_queue: asyncio.Queue = asyncio.Queue()

    # ------------------------------------------------------------------ #
    #  Quantisierung                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _quantize_hue(raw: float) -> int:
        """Rundet einen Hue-Wert (0–359) auf das nächste Vielfache von HUE_STEP."""
        return round(raw / HUE_STEP) * HUE_STEP % 360

    # ------------------------------------------------------------------ #
    #  Queue-Hilfsmethoden (alle non-blocking)                           #
    # ------------------------------------------------------------------ #

    def send(self, item: str, value: str, debounce: bool = False):
        """
        Stellt einen SimpleCommand in die Queue.
        Mit debounce=True wird der Command verworfen, wenn für dieses Item
        innerhalb von DEBOUNCE_MS bereits ein identischer Command gesendet
        wurde. Sinnvoll für Jalousie, Rollladen, Lautstärke etc.
        """
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
        """
        Berechnet den quantisierten Hue-Winkel aus dem Stick und stellt
        ihn bereit – aber nur, wenn:
          a) die Änderung zum letzten gesendeten Wert >= HUE_MIN_DELTA ist
          b) der Worker gerade nicht beschäftigt ist (Backpressure)
        """
        x, y = -x, -y
        if abs(x) < 0.15 and abs(y) < 0.15:
            return  # Deadzone

        raw_hue = (math.degrees(math.atan2(y, x)) + 360 - 90) % 360
        hue     = self._quantize_hue(raw_hue)

        # Zu kleine Änderung? → ignorieren
        if self._last_sent_hue is not None:
            delta = abs(hue - self._last_sent_hue)
            delta = min(delta, 360 - delta)  # kürzerer Weg auf dem Kreis
            if delta < HUE_MIN_DELTA:
                return

        # Worker beschäftigt? → neuesten Wert merken, aber nicht erneut wecken,
        # wenn bereits ein Wert aussteht (Event ist schon gesetzt).
        self._pending_hue = hue
        if not self._hue_busy:
            self._hue_event.set()

    # ------------------------------------------------------------------ #
    #  Radio-Navigation (alles non-blocking via Queue)                   #
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
            if value > 0.5 and not self._lt_triggered:
                self._lt_triggered = True
                self.prev_station()
            elif value <= 0.5:
                self._lt_triggered = False

        elif axis == RT_AXIS:
            if value > 0.5 and not self._rt_triggered:
                self._rt_triggered = True
                self.next_station()
            elif value <= 0.5:
                self._rt_triggered = False

    def _on_button(self, event):
        btn = event.button

        if btn == A_BUTTON:
            self.toggle(self._current_radio_item())
        elif btn == B_BUTTON:
            print("❄️  B → Farbtemperatur DECREASE")
            self.send("iSmartHome_Hue_Lampen_Farbtemperatur", "DECREASE", debounce=True)
        elif btn == X_BUTTON:
            print("💡 X → Hue-Lampe toggle")
            self.toggle("iSmartHome_Hue_Lampen_Schalter")
        elif btn == Y_BUTTON:
            print("🔥 Y → Farbtemperatur INCREASE")
            self.send("iSmartHome_Hue_Lampen_Farbtemperatur", "INCREASE", debounce=True)
        elif btn == LEFT_BUMPER:
            print("⬅️  LB → Vorheriger Raum")
            self.prev_room()
        elif btn == RIGHT_BUMPER:
            print("➡️  RB → Nächster Raum")
            self.next_room()
        elif btn == BACK_BUTTON:
            print("⬅️  BACK → Morgenroutine Ausgangszustand toggle")
            self.toggle("iApplikation_Morgenroutine_Ausgangszustand")
        elif btn == START_BUTTON:
            print("▶️  START → Morgenroutine Start toggle")
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
        """
        Verarbeitet SimpleCommand und ToggleCommand sequenziell.
        Darf beliebig lange awaiten – die Game-Loop läuft unabhängig weiter.
        """
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
        """
        Backpressure + Latest-wins für Hue-Farbbefehle.

        Ablauf:
          1. Warten bis _hue_event gesetzt wird (neuer Wert vom Stick)
          2. _hue_busy = True  → _schedule_hue() schickt keine weiteren Events
          3. Neuesten _pending_hue lesen und senden (await)
          4. Nach dem HTTP-Call: prüfen, ob zwischenzeitlich ein weiterer Wert
             eingetroffen ist (_pending_hue hat sich geändert) → sofort weiter,
             sonst auf das nächste Event warten.
          5. _hue_busy = False
        """
        while True:
            # Auf den nächsten Wert warten
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

                # Hat sich _pending_hue während des HTTP-Calls geändert?
                if self._pending_hue != hue:
                    # Ja → direkt den neuesten Wert senden (kein Event nötig)
                    continue
                else:
                    # Nein → fertig, auf nächstes Event warten
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
                        self._on_axis(event)    # synchron, kein await
                    elif event.type == pygame.JOYBUTTONDOWN:
                        self._on_button(event)  # synchron, kein await
                    elif event.type == pygame.JOYHATMOTION:
                        self._on_dpad(event)    # synchron, kein await

                await asyncio.sleep(0.01)

        finally:
            for t in tasks:
                t.cancel()


# ======================================================================
#  Einstiegspunkt
# ======================================================================

async def main():
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("❌ Kein Controller gefunden!")
        return

    joy = pygame.joystick.Joystick(0)
    joy.init()

    async with AsyncOpenHABClient(URL, PASSWORD) as client:
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
