import math
import asyncio
import pygame
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
#  Command-Typen
# ======================================================================

class SimpleCommand:
    """Ein einzelner OpenHAB-Befehl: item + value."""
    def __init__(self, item: str, value: str):
        self.item  = item
        self.value = value

class ToggleCommand:
    """Liest den State und schaltet dann ON/OFF."""
    def __init__(self, item: str):
        self.item = item

class HueCommand:
    """Farbwert – wird im Worker auf den neuesten Wert reduziert."""
    def __init__(self, hue: int):
        self.hue = hue


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

        # Trigger-Guards (LT/RT nicht dauerfeuern)
        self._lt_triggered = False
        self._rt_triggered = False

        # Hue-State
        self._last_sent_hue: int | None = None

        # ----------------------------------------------------------------
        #  Zwei Queues:
        #  cmd_queue  – normale Befehle, werden der Reihe nach abgearbeitet
        #  hue_queue  – nur Farbwerte; Worker nimmt immer nur den neuesten
        # ----------------------------------------------------------------
        self.cmd_queue: asyncio.Queue = asyncio.Queue()
        self.hue_queue: asyncio.Queue = asyncio.Queue()

    # ------------------------------------------------------------------ #
    #  Hilfsmethoden – nur Befehle in die Queue stellen, kein await!     #
    # ------------------------------------------------------------------ #

    def send(self, item: str, value: str):
        """Stellt einen einfachen Befehl in die Queue (non-blocking)."""
        self.cmd_queue.put_nowait(SimpleCommand(item, value))

    def toggle(self, item: str):
        """Stellt einen Toggle-Befehl in die Queue (non-blocking)."""
        self.cmd_queue.put_nowait(ToggleCommand(item))

    def send_hue(self, hue: int):
        """Stellt einen Hue-Wert in die separate Hue-Queue."""
        self.hue_queue.put_nowait(HueCommand(hue))

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
            self.send(item, direction)

    # ------------------------------------------------------------------ #
    #  Hue-Berechnung (kein await – nur Queue-Eintrag)                   #
    # ------------------------------------------------------------------ #

    def _schedule_hue(self, x: float, y: float):
        x, y = -x, -y
        if abs(x) < 0.15 and abs(y) < 0.15:
            return
        hue = round((math.degrees(math.atan2(y, x)) + 360 - 90) % 360)
        if self._last_sent_hue is not None and abs(hue - self._last_sent_hue) < 3:
            return
        self.send_hue(hue)

    # ------------------------------------------------------------------ #
    #  Event-Verarbeitung – NUR synchron, kein await!                    #
    # ------------------------------------------------------------------ #

    def _on_axis(self, event):
        axis  = event.axis
        value = event.value

        if axis == LEFT_STICK_X:
            if value < -0.5:
                self.send("iSmartHome_Jalousie_Steuerung", "DOWN")
            elif value > 0.5:
                self.send("iSmartHome_Jalousie_Steuerung", "UP")
            else:
                self.send("iSmartHome_Jalousie_Steuerung", "STOP")

        elif axis == LEFT_STICK_Y:
            if value < -0.5:
                self.send("iSmartHome_Somfy_Rollladen_Steuerung", "UP")
            elif value > 0.5:
                self.send("iSmartHome_Somfy_Rollladen_Steuerung", "DOWN")
            else:
                self.send("iSmartHome_Somfy_Rollladen_Steuerung", "STOP")

        elif axis in (RIGHT_STICK_X, RIGHT_STICK_Y):
            rx = self.joystick.get_axis(RIGHT_STICK_X)
            ry = self.joystick.get_axis(RIGHT_STICK_Y)
            self._schedule_hue(rx, ry)

        elif axis == LT_AXIS:
            if value > 0.5 and not self._lt_triggered:
                self._lt_triggered = True
                print("🎚 LT → Vorheriger Sender")
                self.prev_station()
            elif value <= 0.5:
                self._lt_triggered = False

        elif axis == RT_AXIS:
            if value > 0.5 and not self._rt_triggered:
                self._rt_triggered = True
                print("🎚 RT → Nächster Sender")
                self.next_station()
            elif value <= 0.5:
                self._rt_triggered = False

    def _on_button(self, event):
        btn = event.button

        if btn == A_BUTTON:
            self.toggle(self._current_radio_item())

        elif btn == B_BUTTON:
            print("❄️  B → Farbtemperatur DECREASE")
            self.send("iSmartHome_Hue_Lampen_Farbtemperatur", "DECREASE")

        elif btn == X_BUTTON:
            print("💡 X → Hue-Lampe toggle")
            self.toggle("iSmartHome_Hue_Lampen_Schalter")

        elif btn == Y_BUTTON:
            print("🔥 Y → Farbtemperatur INCREASE")
            self.send("iSmartHome_Hue_Lampen_Farbtemperatur", "INCREASE")

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
            self.send("iSmartHome_Hue_Lampen_Helligkeit", "DECREASE")
        elif dpad == DPAD_RIGHT:
            print("💡➡️  D-Pad RIGHT → Helligkeit INCREASE")
            self.send("iSmartHome_Hue_Lampen_Helligkeit", "INCREASE")

    # ------------------------------------------------------------------ #
    #  Worker-Tasks (laufen parallel zur Game-Loop)                      #
    # ------------------------------------------------------------------ #

    async def _cmd_worker(self):
        """
        Verarbeitet SimpleCommand und ToggleCommand aus der cmd_queue.
        Läuft unabhängig von der Game-Loop – darf beliebig lange await'en.
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

            self.cmd_queue.task_done()

    async def _hue_worker(self):
        """
        Latest-wins: Leert die hue_queue komplett, nimmt nur den neuesten
        Wert und sendet ihn. Danach 100 ms Pause (Bridge-Schutz).
        """
        while True:
            cmd = await self.hue_queue.get()

            # Queue leeren – nur den letzten Wert behalten
            while not self.hue_queue.empty():
                cmd = self.hue_queue.get_nowait()

            hue = cmd.hue
            if self._last_sent_hue is None or abs(hue - self._last_sent_hue) >= 3:
                command = f"{hue},100,70"
                print(f"🎨 Hue={hue}° → {command}")
                try:
                    await self.items.sendCommand("iSmartHome_Hue_Lampen_Farbe", command)
                    self._last_sent_hue = hue
                except Exception as e:
                    print(f"⚠️  Hue-Fehler: {e}")

            await asyncio.sleep(0.1)  # max 10 Hue-Befehle/s → Bridge stabil

    # ------------------------------------------------------------------ #
    #  Game-Loop – blockiert NIE auf Netzwerk                            #
    # ------------------------------------------------------------------ #

    async def run_loop(self):
        print("🎮 Controller-Loop gestartet …")

        # Worker-Tasks starten
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
                        self._on_axis(event)       # synchron, kein await
                    elif event.type == pygame.JOYBUTTONDOWN:
                        self._on_button(event)     # synchron, kein await
                    elif event.type == pygame.JOYHATMOTION:
                        self._on_dpad(event)       # synchron, kein await

                await asyncio.sleep(0.01)  # gibt Event-Loop Luft zum Atmen

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
