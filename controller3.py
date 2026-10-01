import math
import asyncio
import aiohttp
import pygame
import time


# === OpenHAB Async Client ===
class AsyncOpenHABClient:
    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.auth = aiohttp.BasicAuth(username, password)
        self.session = None

    async def connect(self):
        self.session = aiohttp.ClientSession(auth=self.auth)

    async def close(self):
        if self.session:
            await self.session.close()

    async def send_command(self, item, command):
        if not self.session:
            raise RuntimeError("Session not initialized. Call connect() first.")
        endpoint = f"{self.url}/rest/items/{item}"
        async with self.session.post(endpoint, data=command) as resp:
            if resp.status != 200:
                print(f"⚠️ Fehler bei sendCommand({item}, {command}): {resp.status}")
            return resp.status

    async def get_state(self, item):
        if not self.session:
            raise RuntimeError("Session not initialized. Call connect() first.")
        endpoint = f"{self.url}/rest/items/{item}/state"
        async with self.session.get(endpoint) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                print(f"⚠️ Fehler bei get_state({item}): {resp.status}")
                return "UNKNOWN"


# === Controller ===
class Controller:
    def __init__(self, oh_client: AsyncOpenHABClient):
        pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() == 0:
            print("❌ Kein Controller gefunden.")
            pygame.quit()
            raise SystemExit

        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()

        self.client = oh_client

        self.rooms = ["iKonferenz", "iKueche", "iBad", "iIoT", "iMultimedia"]
        self.stations = ["SWR3", "bigFM_BW", "Energy_Stuttgart", "Radio_Regenbogen", "Antenne1", "DASDING"]

        self.room_index = 0
        self.station_index = 0
        self.last_hue = None
        self.last_command_time = 0

        print(f"🔊 Aktueller Raum: {self.rooms[self.room_index]}, Sender: {self.stations[self.station_index]}")

    def get_current_item(self):
        return f"{self.rooms[self.room_index]}_Webradio_{self.stations[self.station_index]}"

    async def stop_current_radio(self):
        item = self.get_current_item()
        print(f"🛑 Stoppe {item}")
        await self.client.send_command(item, "OFF")

    async def start_current_radio(self):
        item = self.get_current_item()
        print(f"▶️ Starte {item}")
        await self.client.send_command(item, "ON")

    async def next_room(self):
        await self.stop_current_radio()
        self.room_index = (self.room_index + 1) % len(self.rooms)
        print(f"🏠 Raum gewechselt zu: {self.rooms[self.room_index]}")
        await self.start_current_radio()

    async def prev_room(self):
        await self.stop_current_radio()
        self.room_index = (self.room_index - 1) % len(self.rooms)
        print(f"🏠 Raum gewechselt zu: {self.rooms[self.room_index]}")
        await self.start_current_radio()

    async def next_station(self):
        await self.stop_current_radio()
        self.station_index = (self.station_index + 1) % len(self.stations)
        print(f"📻 Sender gewechselt zu: {self.stations[self.station_index]}")
        await self.start_current_radio()

    async def prev_station(self):
        await self.stop_current_radio()
        self.station_index = (self.station_index - 1) % len(self.stations)
        print(f"📻 Sender gewechselt zu: {self.stations[self.station_index]}")
        await self.start_current_radio()

    async def adjust_volume(self, direction):
        room = self.rooms[self.room_index]
        volume_items = {
            "iKonferenz": "iKonferenz_Sonos_Playbar_Lautstaerke",
            "iKueche": "iKueche_Sonos_Lautsprecher_Lautstaerke",
            "iBad": "iBad_Sonos_Lautsprecher_Lautstaerke",
            "iIoT": "iIoT_Sonos_Lautsprecher_Lautstaerke",
            "iMultimedia": "iMultimedia_Sonos_Lautsprecher_Lautstaerke"
        }

        if room not in volume_items:
            print(f"⚠️ Kein Lautstärke-Item für Raum {room} gefunden")
            return

        item = volume_items[room]
        print(f"🔊 {direction} Lautstärke: {item}")
        await self.client.send_command(item, direction)

    async def handle_color_stick(self, x, y):
        y = -y
        x = -x
        deadzone = 0.1

        if abs(x) < deadzone and abs(y) < deadzone:
            return

        hue = (math.degrees(math.atan2(y, x)) + 360 - 90) % 360
        hue = round(hue)

        if self.last_hue is not None and abs(hue - self.last_hue) < 5:
            return

        now = time.time() * 1000
        if now - self.last_command_time < 200:
            return

        command = f"{hue},100,70"
        print(f"🎨 Hue-Stick → {command}")
        await self.client.send_command("iSmartHome_Hue_Lampen_Farbe", command)

        self.last_hue = hue
        self.last_command_time = now

    async def handle_event(self, event):
        # JOYAXISMOTION
        if event.type == pygame.JOYAXISMOTION:
            if event.axis in [2, 3]:
                right_x = self.joystick.get_axis(2)
                right_y = self.joystick.get_axis(3)
                await self.handle_color_stick(right_x, right_y)
            elif event.axis == 4 and event.value > 0.5:
                await self.prev_station()
            elif event.axis == 5 and event.value > 0.5:
                await self.next_station()

        # JOYBUTTONDOWN
        elif event.type == pygame.JOYBUTTONDOWN:
            if event.button == 0:  # A
                item = self.get_current_item()
                state = await self.client.get_state(item)
                command = "OFF" if "ON" in state else "ON"
                print(f"🎮 A pressed → {item}: {command}")
                await self.client.send_command(item, command)

            elif event.button == 1:  # B
                print("B gedrückt → Farbtemperatur DECREASE")
                await self.client.send_command("iSmartHome_Hue_Lampen_Farbtemperatur", "DECREASE")

            elif event.button == 2:  # X
                print("X gedrückt → Schalte Hue-Lampe")
                item = "iSmartHome_Hue_Lampen_Schalter"
                state = await self.client.get_state(item)
                command = "OFF" if "ON" in state else "ON"
                await self.client.send_command(item, command)

            elif event.button == 3:  # Y
                print("Y gedrückt → Farbtemperatur INCREASE")
                await self.client.send_command("iSmartHome_Hue_Lampen_Farbtemperatur", "INCREASE")

            elif event.button == 4:  # LB
                await self.prev_room()
            elif event.button == 5:  # RB
                await self.next_room()

        # JOYHATMOTION (D-Pad)
        elif event.type == pygame.JOYHATMOTION:
            dpad = self.joystick.get_hat(0)
            if dpad == (0, 1):
                await self.adjust_volume("INCREASE")
            elif dpad == (0, -1):
                await self.adjust_volume("DECREASE")
            elif dpad == (-1, 0):
                await self.client.send_command("iSmartHome_Hue_Lampen_Helligkeit", "DECREASE")
            elif dpad == (1, 0):
                await self.client.send_command("iSmartHome_Hue_Lampen_Helligkeit", "INCREASE")

    async def control_loop(self):
        print("🎮 Controller gestartet (async)")
        while True:
            for event in pygame.event.get():
                await self.handle_event(event)
            await asyncio.sleep(0.01)  # 10 ms Delay, CPU freundlich


# === Main ===
async def main():
    client = AsyncOpenHABClient(
        url="http://192.168.0.5:8080",
        username="openHABAdmin",
        password="hJem2jz6"
    )
    await client.connect()

    controller = Controller(client)
    try:
        await controller.control_loop()
    finally:
        await client.close()
        pygame.quit()

if __name__ == "__main__":
    asyncio.run(main())
