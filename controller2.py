import math
import pygame
from openhab import OpenHABClient, Items


client = OpenHABClient(url="http://192.168.0.5:8080", username="openHABAdmin", password="hJem2jz6")
items = Items(client)

# JOYBUTTONDOWN / JOYBUTTONUP
A_BUTTON = 0
B_BUTTON = 1
X_BUTTON = 2
Y_BUTTON = 3
LEFT_BUMPER = 4
RIGHT_BUMPER = 5
BACK_BUTTON = 6
START_BUTTON = 7
L_STICK_IN = 8
R_STICK_IN = 9
GUIDE_BUTTON = 10

# JOYHATMOTION
DPAD_RELEASED = (0, 0)
DPAD_UP = (0, 1)
DPAD_DOWN = (0, -1)
DPAD_LEFT = (-1, 0)
DPAD_RIGHT = (1, 0)

# JOYAXISMOTION
LEFT_STICK_X_AXIS = 0
LEFT_STICK_Y_AXIS = 1
RIGHT_STICK_X_AXIS = 2
RIGHT_STICK_Y_AXIS = 3
LT_STICK = 4
RT_STICK = 5


class Controller(object):
    def __init__(self):
        # Pygame init
        pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() == 0:
            print("No controller found.")
            pygame.quit()
            exit()

        self.joystick = pygame.joystick.Joystick(0) # 0 steht für 1. Spieler. Die XBox-Taste kennt 1, 2, 3 und 4.
        self.joystick.init()

        # === Logik für Raum & Sender ===
        self.rooms = ["iKonferenz", "iKueche", "iBad", "iIoT", "iMultimedia"]
        self.stations = ["SWR3", "bigFM_BW", "Energy_Stuttgart", "Radio_Regenbogen", "Antenne1", "DASDING"]

        self.room_index = 0
        self.station_index = 0

        print(
            f"🔊 Aktueller Raum: {self.rooms[self.room_index]}, Sender: {self.stations[self.station_index]}")

    def get_current_item(self):
        """Kombiniert den aktuellen Raum & Sender zum OpenHAB-Itemnamen"""
        return f"{self.rooms[self.room_index]}_Webradio_{self.stations[self.station_index]}"

    def stop_current_radio(self):
        """Schaltet das aktuelle Radio-Item auf OFF"""
        item = self.get_current_item()
        print(f"🛑 Stoppe {item}")
        items.sendCommand(item, "OFF")

    def start_current_radio(self):
        """Schaltet das aktuelle Radio-Item auf ON"""
        item = self.get_current_item()
        print(f"▶️ Starte {item}")
        items.sendCommand(item, "ON")

    def next_room(self):
        """Wechselt zum nächsten Raum"""
        self.stop_current_radio()
        self.room_index = (self.room_index + 1) % len(self.rooms)
        print(f"🏠 Raum gewechselt zu: {self.rooms[self.room_index]}")
        self.start_current_radio()

    def prev_room(self):
        """Wechselt zum vorherigen Raum"""
        self.stop_current_radio()
        self.room_index = (self.room_index - 1) % len(self.rooms)
        print(f"🏠 Raum gewechselt zu: {self.rooms[self.room_index]}")
        self.start_current_radio()

    def next_station(self):
        """Wechselt zum nächsten Radiosender"""
        self.stop_current_radio()
        self.station_index = (self.station_index + 1) % len(self.stations)
        print(f"📻 Sender gewechselt zu: {self.stations[self.station_index]}")
        self.start_current_radio()

    def prev_station(self):
        """Wechselt zum vorherigen Radiosender"""
        self.stop_current_radio()
        self.station_index = (self.station_index - 1) % len(self.stations)
        print(f"📻 Sender gewechselt zu: {self.stations[self.station_index]}")
        self.start_current_radio()

    def adjust_volume(self, direction):
        """
        direction: 'INCREASE' oder 'DECREASE'
        Sendet Lautstärkeänderung für den aktuellen Raum
        """
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
        response = items.sendCommand(item, direction)
        print(response)

    def controll_loop(self):
        # Main loop
        while True:
            # Query events
            for event in pygame.event.get():
                if event.type == pygame.JOYAXISMOTION:
                    # Movement of the axes
                    if event.axis == LEFT_STICK_X_AXIS:
                        # Axis 0 (X-axis of the left stick)
                        # Jalousien links/rechts (Neigung)
                        left_x_axis = event.value
                        print("X axis (Jalousien):", left_x_axis)

                        if left_x_axis < -0.5:
                            print("⬅️ Jalousien RUNTER")
                            items.sendCommand("iSmartHome_Jalousie_Steuerung", "DOWN")
                        elif left_x_axis > 0.5:
                            print("➡️ Jalousien HOCH")
                            items.sendCommand("iSmartHome_Jalousie_Steuerung", "UP")
                        else:
                            print("⏹ Jalousien STOP")
                            items.sendCommand("iSmartHome_Jalousie_Steuerung", "STOP")
                    elif event.axis == LEFT_STICK_X_AXIS:
                        left_y_axis = event.value
                        print("Y axis (Rollläden):", left_y_axis)

                        if left_y_axis < -0.5:
                            print("⬆️ Rollläden HOCH")
                            items.sendCommand("iSmartHome_Somfy_Rollladen_Steuerung", "UP")
                        elif left_y_axis > 0.5:
                            print("⬇️ Rollläden RUNTER")
                            items.sendCommand("iSmartHome_Somfy_Rollladen_Steuerung", "DOWN")
                        else:
                            print("⏹ Rollläden STOP")
                            items.sendCommand("iSmartHome_Somfy_Rollladen_Steuerung", "STOP")
                    elif event.axis in [RIGHT_STICK_X_AXIS, RIGHT_STICK_Y_AXIS]:
                        x = self.joystick.get_axis(RIGHT_STICK_X_AXIS)
                        y = -self.joystick.get_axis(RIGHT_STICK_Y_AXIS)

                        if abs(x) > 0.1 or abs(y) > 0.1:
                            hue = (math.degrees(math.atan2(y, x)) + 360) % 360
                            command = f"{hue:.0f},100,70"
                            print(f"🎨 Hue={hue:.0f}° → Farbe geändert: {command}")
                            items.sendCommand("iSmartHome_Hue_Lampen_Farbe", command)
                    elif event.axis == LT_STICK:
                        # Axis 4 (LT button)
                        if event.value > 0.5:
                            print("🎚 LT gedrückt → Vorheriger Sender")
                            self.prev_station()
                    elif event.axis == RT_STICK:
                        # Axis 5 (RT button)
                        if event.value > 0.5:
                            print("🎚 RT gedrückt → Nächster Sender")
                            self.next_station()

                elif event.type == pygame.JOYBUTTONDOWN:
                    # button pressed
                    if event.button == A_BUTTON:
                        # Button 0 (A button)
                        # Start/Stop aktuelle Wiedergabe
                        item = self.get_current_item()
                        state = items.getItemState(item)
                        print(f"🎮 A pressed – aktueller Zustand: {state}")
                        command = "OFF" if "ON" in state else "ON"
                        response = items.sendCommand(item, command)
                        print(response)
                    elif event.button == B_BUTTON:
                        # Button 1 (B button)
                        print("❄️ B gedrückt → Farbtemperatur DECREASE (kälter)")
                        items.sendCommand("iSmartHome_Hue_Lampen_Farbtemperatur", "DECREASE")
                    elif event.button == X_BUTTON:
                        # Button 2 (X button)
                        print("X button pressed")
                        item = "iSmartHome_Hue_Lampen_Schalter"
                        state = items.getItemState(item)
                        print(state)
                        if "ON" in state:
                            command = "OFF"
                        else:
                            command = "ON"
                        response = items.sendCommand(item, command)
                        print(response)
                    elif event.button == Y_BUTTON:
                        # Button 3 (Y button)
                        print("🔥 Y gedrückt → Farbtemperatur INCREASE (wärmer)")
                        items.sendCommand("iSmartHome_Hue_Lampen_Farbtemperatur", "INCREASE")
                    elif event.button == LEFT_BUMPER:
                        # Button 4 (Left bumper)
                        # Raum zurück
                        print("🎮 LEFT_BUMPER gedrückt → Vorheriger Raum")
                        self.prev_room()
                    elif event.button == RIGHT_BUMPER:
                        # Button 5 (Right bumper)
                        print("🎮 RIGHT_BUMPER gedrückt → Nächster Raum")
                        self.next_room()
                    elif event.button == BACK_BUTTON:
                        # Button 6 (Back button)
                        print("Back button pressed")
                        item = "iApplikation_Morgenroutine_Ausgangszustand"
                        state = items.getItemState(item)
                        print(state)
                        if "ON" in state:
                            command = "OFF"
                        else:
                            command = "ON"
                        response = items.sendCommand(item, command)
                        print(response)
                    elif event.button == START_BUTTON:
                        # Button 7 (Start button)
                        print("Start button pressed")
                        item = "iApplikation_Morgenroutine_Start"
                        state = items.getItemState(item)
                        print(state)
                        if "ON" in state:
                            command = "OFF"
                        else:
                            command = "ON"
                        response = items.sendCommand(item, command)
                        print(response)
                    elif event.button == L_STICK_IN:
                        # Button 8 (L-Stick in)
                        print("L-Stick in pressed")
                    elif event.button == R_STICK_IN:
                        # Button 9 (R-Stick in)
                        print("R-Stick in button pressed")
                    elif event.button == GUIDE_BUTTON:
                        # Button 10 (Guide button)
                        print("Guide button pressed")

                elif event.type == pygame.JOYBUTTONUP:
                    # button released
                    if event.button == A_BUTTON:
                        # Button 0 (A button)
                        print("A button released")
                    elif event.button == B_BUTTON:
                        # Button 1 (B button)
                        print("B button released")
                    elif event.button == X_BUTTON:
                        # Button 2 (X button)
                        print("X button released")
                    elif event.button == Y_BUTTON:
                        # Button 3 (Y button)
                        print("Y button released")
                    elif event.button == LEFT_BUMPER:
                        # Button 4 (Left bumper)
                        print("Left bumper button released")
                    elif event.button == RIGHT_BUMPER:
                        # Button 5 (Right bumper)
                        print("Right button released")
                    elif event.button == BACK_BUTTON:
                        # Button 6 (Back button)
                        print("Back button released")
                    elif event.button == START_BUTTON:
                        # Button 7 (Start button)
                        print("Start button released")
                    elif event.button == L_STICK_IN:
                        # Button 8 (L-Stick in)
                        print("L-Stick in released")
                    elif event.button == R_STICK_IN:
                        # Button 9 (R-Stick in)
                        print("R-Stick in button released")
                    elif event.button == GUIDE_BUTTON:
                        # Button 10 (Guide button)
                        print("Guide button released")

                # Check dpad/hat event
                elif event.type == pygame.JOYHATMOTION:
                    dpad = self.joystick.get_hat(0)

                    if dpad == DPAD_RELEASED:
                        print("dpad released")
                    elif dpad == DPAD_UP:
                        print("🔼 D-Pad UP → Volume INCREASE")
                        self.adjust_volume("INCREASE")
                    elif dpad == DPAD_DOWN:
                        print("🔽 D-Pad DOWN → Volume DECREASE")
                        self.adjust_volume("DECREASE")
                    elif dpad == DPAD_LEFT:
                        print("💡⬅️ D-Pad LEFT → Brightness DECREASE")
                        response = items.sendCommand("iSmartHome_Hue_Lampen_Helligkeit", "DECREASE")
                        print(response)
                    elif dpad == DPAD_RIGHT:
                        print("💡➡️ D-Pad RIGHT → Brightness INCREASE")
                        response = items.sendCommand("iSmartHome_Hue_Lampen_Helligkeit", "INCREASE")
                        print(response)
            # Waiting time to reduce CPU load
            pygame.time.wait(10)


if __name__ == "__main__":
    controller = Controller()
    controller.controll_loop()
