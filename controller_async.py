import math
import asyncio
import pygame
import time
from openhab import AsyncOpenHABClient
from openhab import AsyncItems

# === KONFIGURATION ===
URL = "http://192.168.0.5:8080"
USERNAME = "openHABAdmin"
PASSWORD = ""

# Button Mapping (XBox)
A_BUTTON      = 0
B_BUTTON      = 1
X_BUTTON      = 2
Y_BUTTON      = 3
LEFT_BUMPER   = 4
RIGHT_BUMPER  = 5
BACK_BUTTON   = 6
START_BUTTON  = 7
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


class OpenHABController:
    def __init__(self, items_api: AsyncItems, joystick):
        self.items    = items_api
        self.joystick = joystick

        self.rooms    = ["iKonferenz", "iKueche", "iBad", "iIoT", "iMultimedia"]
        self.stations = ["SWR3", "bigFM_BW", "Energy_Stuttgart", "Radio_Regenbogen", "Antenne1", "DASDING"]

        self.volume_items = {
            "iKonferenz":  "iKonferenz_Sonos_Playbar_Lautstaerke",
            "iKueche":     "iKueche_Sonos_Lautsprecher_Lautstaerke",
            "iBad":        "iBad_Sonos_Lautsprecher_Lautstaerke",
            "iIoT":        "iIoT_Sonos_Lautsprecher_Lautstaerke",
            "iMultimedia": "iMultimedia_Sonos_Lautsprecher_Lautstaerke",
        }

        self.room_index    = 0
        self.station_index = 0

        # Hue-Optimierung: Latest-wins Queue
        # Enthält immer nur den neuesten Hue-Wert; ältere werden verworfen.
        self._pending_hue: int | None = None
        self._hue_event = asyncio.Event()
        self._last_sent_hue: int | None = None

        # Trigger-Guard für LT/RT (verhindert Dauerfeuern beim Halten)
        self._lt_triggered = False
        self._rt_triggered = False

        print(f"🔊 Raum: {self.rooms[self.room_index]} | Sender: {self.stations[self.station_index]}")

    # ------------------------------------------------------------------ #
    #  Radio-Hilfsmethoden                                                #
    # ------------------------------------------------------------------ #

    def get_current_item(self) -> str:
        return f"{self.rooms[self.room_index]}_Webradio_{self.stations[self.station_index]}"

    async def _stop_radio(self):
        item = self.get_current_item()
        print(f"🛑 Stoppe {item}")
        await self.items.sendCommand(item, "OFF")

    async def _start_radio(self):
        item = self.get_current_item()
        print(f"▶️  Starte {item}")
        await self.items.sendCommand(item, "ON")

    async def next_room(self):
        await self._stop_radio()
        self.room_index = (self.room_index + 1) % len(self.rooms)
        print(f"🏠 Raum → {self.rooms[self.room_index]}")
        await self._start_radio()

    async def prev_room(self):
        await self._stop_radio()
        self.room_index = (self.room_index - 1) % len(self.rooms)
        print(f"🏠 Raum → {self.rooms[self.room_index]}")
        await self._start_radio()

    async def next_station(self):
        await self._stop_radio()
        self.station_index = (self.station_index + 1) % len(self.stations)
        print(f"📻 Sender → {self.stations[self.station_index]}")
        await self._start_radio()

    async def prev_station(self):
        await self._stop_radio()
        self.station_index = (self.station_index - 1) % len(self.stations)
        print(f"📻 Sender → {self.stations[self.station_index]}")
        await self._start_radio()

    # ------------------------------------------------------------------ #
    #  Lautstärke                                                         #
    # ------------------------------------------------------------------ #

    async def adjust_volume(self, direction: str):
        room = self.rooms[self.room_index]
        item = self.volume_items.get(room)
        if not item:
            print(f"⚠️  Kein Lautstärke-Item für {room}")
            return
        print(f"🔊 {direction} → {item}")
        await self.items.sendCommand(item, direction)

    # ------------------------------------------------------------------ #
    #  Hue – Latest-wins Worker                                           #
    #                                                                     #
    #  Der Worker läuft als eigenständiger asyncio-Task.                  #
    #  handle_hue_stick() schreibt nur den neuesten Wert und              #
    #  setzt ein Event; der Worker sendet ihn und wartet dann             #
    #  auf das nächste Signal. Dadurch werden niemals alte                #
    #  Anfragen in einer Queue aufgestaut.                                #
    # ------------------------------------------------------------------ #

    async def _hue_worker(self):
        """Sendet immer nur den zuletzt gesetzten Hue-Wert."""
        while True:
            await self._hue_event.wait()
            self._hue_event.clear()

            hue = self._pending_hue
            if hue is None:
                continue

            # Kleine Änderungen ignorieren (< 3°) um Rauschen zu dämpfen
            if self._last_sent_hue is not None and abs(hue - self._last_sent_hue) < 3:
                continue

            command = f"{hue},100,70"
            print(f"🎨 Hue={hue}° → {command}")
            await self.items.sendCommand("iSmartHome_Hue_Lampen_Farbe", command)
            self._last_sent_hue = hue

            # Kurze Pause nach dem Senden – verhindert Überlastung der Bridge.
            # Während dieser Zeit darf der Worker weitere Events aufnehmen,
            # sendet aber erst nach Ablauf wieder (throttle ≈ 100 ms).
            await asyncio.sleep(0.1)

    def _schedule_hue(self, x: float, y: float):
        """Berechnet den Hue-Winkel und stellt ihn zur Übertragung bereit."""
        # Koordinaten so drehen, dass 12 Uhr = Rot (hue=0)
        x, y = -x, -y
        deadzone = 0.15

        if abs(x) < deadzone and abs(y) < deadzone:
            return  # Stick in Ruheposition → nichts tun

        hue = round((math.degrees(math.atan2(y, x)) + 360 - 90) % 360)
        self._pending_hue = hue
        self._hue_event.set()  # Worker wecken (non-blocking)

    # ------------------------------------------------------------------ #
    #  Event-Handler                                                      #
    # ------------------------------------------------------------------ #

    async def _handle_axis(self, event):
        axis  = event.axis
        value = event.value

        # Linker Stick X → Jalousien
        if axis == LEFT_STICK_X:
            if value < -0.5:
                print("⬅️  Jalousien DOWN")
                await self.items.sendCommand("iSmartHome_Jalousie_Steuerung", "DOWN")
            elif value > 0.5:
                print("➡️  Jalousien UP")
                await self.items.sendCommand("iSmartHome_Jalousie_Steuerung", "UP")
            else:
                await self.items.sendCommand("iSmartHome_Jalousie_Steuerung", "STOP")

        # Linker Stick Y → Rollläden
        elif axis == LEFT_STICK_Y:
            if value < -0.5:
                print("⬆️  Rollläden UP")
                await self.items.sendCommand("iSmartHome_Somfy_Rollladen_Steuerung", "UP")
            elif value > 0.5:
                print("⬇️  Rollläden DOWN")
                await self.items.sendCommand("iSmartHome_Somfy_Rollladen_Steuerung", "DOWN")
            else:
                await self.items.sendCommand("iSmartHome_Somfy_Rollladen_Steuerung", "STOP")

        # Rechter Stick → Farbe (non-blocking via Worker)
        elif axis in (RIGHT_STICK_X, RIGHT_STICK_Y):
            rx = self.joystick.get_axis(RIGHT_STICK_X)
            ry = self.joystick.get_axis(RIGHT_STICK_Y)
            self._schedule_hue(rx, ry)  # kein await – sofort zurück

        # LT → vorheriger Sender (Trigger-Guard verhindert Dauerfeuern)
        elif axis == LT_AXIS:
            if value > 0.5 and not self._lt_triggered:
                self._lt_triggered = True
                print("🎚  LT → Vorheriger Sender")
                await self.prev_station()
            elif value <= 0.5:
                self._lt_triggered = False

        # RT → nächster Sender
        elif axis == RT_AXIS:
            if value > 0.5 and not self._rt_triggered:
                self._rt_triggered = True
                print("🎚  RT → Nächster Sender")
                await self.next_station()
            elif value <= 0.5:
                self._rt_triggered = False

    async def _handle_button(self, event):
        btn = event.button

        if btn == A_BUTTON:
            item  = self.get_current_item()
            state = await self.items.getItemState(item)
            cmd   = "OFF" if "ON" in state else "ON"
            print(f"🎮 A → {item}: {cmd}")
            await self.items.sendCommand(item, cmd)

        elif btn == B_BUTTON:
            print("❄️  B → Farbtemperatur DECREASE")
            await self.items.sendCommand("iSmartHome_Hue_Lampen_Farbtemperatur", "DECREASE")

        elif btn == X_BUTTON:
            item  = "iSmartHome_Hue_Lampen_Schalter"
            state = await self.items.getItemState(item)
            cmd   = "OFF" if "ON" in state else "ON"
            print(f"💡 X → Hue-Lampe {cmd}")
            await self.items.sendCommand(item, cmd)

        elif btn == Y_BUTTON:
            print("🔥 Y → Farbtemperatur INCREASE")
            await self.items.sendCommand("iSmartHome_Hue_Lampen_Farbtemperatur", "INCREASE")

        elif btn == LEFT_BUMPER:
            print("⬅️  LB → Vorheriger Raum")
            await self.prev_room()

        elif btn == RIGHT_BUMPER:
            print("➡️  RB → Nächster Raum")
            await self.next_room()

        elif btn == BACK_BUTTON:
            item  = "iApplikation_Morgenroutine_Ausgangszustand"
            state = await self.items.getItemState(item)
            cmd   = "OFF" if "ON" in state else "ON"
            print(f"⬅️  BACK → Morgenroutine Ausgangszustand {cmd}")
            await self.items.sendCommand(item, cmd)

        elif btn == START_BUTTON:
            item  = "iApplikation_Morgenroutine_Start"
            state = await self.items.getItemState(item)
            cmd   = "OFF" if "ON" in state else "ON"
            print(f"▶️  START → Morgenroutine Start {cmd}")
            await self.items.sendCommand(item, cmd)

    async def _handle_dpad(self, event):
        dpad = self.joystick.get_hat(0)

        if dpad == DPAD_UP:
            await self.adjust_volume("INCREASE")
        elif dpad == DPAD_DOWN:
            await self.adjust_volume("DECREASE")
        elif dpad == DPAD_LEFT:
            print("💡⬅️  D-Pad LEFT → Helligkeit DECREASE")
            await self.items.sendCommand("iSmartHome_Hue_Lampen_Helligkeit", "DECREASE")
        elif dpad == DPAD_RIGHT:
            print("💡➡️  D-Pad RIGHT → Helligkeit INCREASE")
            await self.items.sendCommand("iSmartHome_Hue_Lampen_Helligkeit", "INCREASE")

    # ------------------------------------------------------------------ #
    #  Haupt-Loop                                                         #
    # ------------------------------------------------------------------ #

    async def run_loop(self):
        print("🎮 Controller-Loop gestartet …")

        # Hue-Worker als Hintergrund-Task starten
        hue_task = asyncio.create_task(self._hue_worker())

        try:
            while True:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return
                    elif event.type == pygame.JOYAXISMOTION:
                        await self._handle_axis(event)
                    elif event.type == pygame.JOYBUTTONDOWN:
                        await self._handle_button(event)
                    elif event.type == pygame.JOYHATMOTION:
                        await self._handle_dpad(event)

                await asyncio.sleep(0.01)
        finally:
            hue_task.cancel()


# ====================================================================== #
#  Einstiegspunkt                                                        #
# ====================================================================== #

async def main():
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
