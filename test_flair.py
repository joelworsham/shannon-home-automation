"""Manual Flair smoke test; reads live devices and doesn't print credentials."""

import os
from dotenv import load_dotenv
from flair_client import make_client

load_dotenv()
client = make_client(
    client_id=os.environ["FLAIR_CLIENT_ID"],
    client_secret=os.environ["FLAIR_CLIENT_SECRET"],
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
bedroom = next(r for r in rooms if r.attributes.get("name") == "Bedroom")
c = bedroom.attributes.get("current-temperature-c")
print(f"Bedroom temperature: {float(c) * 9 / 5 + 32:.1f}°F" if c is not None else "Bedroom temperature unavailable")
print(f"Bedroom humidity: {bedroom.attributes.get('current-humidity')}%")
print(f"Flair room state updated: {bedroom.attributes.get('state-updated-at')}")
if pucks:
    reading = pucks[0].get_rel("current-reading")
    if isinstance(reading, list):
        reading = reading[0] if reading else None
    if reading is not None:
        for key, value in reading.attributes.items():
            if any(s in key.lower() for s in ("temp", "humid", "time", "date", "updated")):
                print(f"Puck {key}: {value}")
