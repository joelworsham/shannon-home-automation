
import os
from flair_client import make_client
from dotenv import load_dotenv

load_dotenv()

client_id = os.environ["FLAIR_CLIENT_ID"]
client_secret = os.environ["FLAIR_CLIENT_SECRET"]

client = make_client(
    client_id=client_id,
    client_secret=client_secret,
    root="https://api.flair.co/",
    user_agent="BedroomAutomation/0.1.0",
)

rooms = client.get("rooms")
pucks = client.get("pucks")

print("Authentication successful!")
print(f"Found {len(rooms)} room(s)")
print(f"Found {len(pucks)} original Puck(s)")

for room in rooms:
    print("Room:", room.attributes.get("name"))

#####


bedroom = next(
    room for room in rooms
    if room.attributes.get("name") == "Bedroom"
)

temp_c = bedroom.attributes.get("current-temperature-c")
humidity = bedroom.attributes.get("current-humidity")
updated = bedroom.attributes.get("state-updated-at")

if temp_c is not None:
    temp_f = temp_c * 9 / 5 + 32
    print(f"Bedroom temperature: {temp_f:.1f}°F")
else:
    print("Bedroom temperature: unavailable")

print(f"Bedroom humidity: {humidity}%")
print(f"Last updated: {updated}")


#####


reading = pucks[0].get_rel("current-reading")

if isinstance(reading, list):
    reading = reading[0] if reading else None

if reading is None:
    print("No Puck reading available")
else:
    for key, value in reading.attributes.items():
        if any(term in key.lower() for term in (
            "temp", "humid", "time", "date", "updated"
        )):
            print(f"{key}: {value}")
