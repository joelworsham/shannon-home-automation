
import os
import requests
from dotenv import load_dotenv
from flair_client import make_client

load_dotenv()

# Initial settings
TARGET_TEMP = 60
RESTART_TEMP = 64
MIN_OUTDOOR_DIFFERENCE = 5


def get_bedroom():
    client = make_client(
        client_id=os.environ["FLAIR_CLIENT_ID"],
        client_secret=os.environ["FLAIR_CLIENT_SECRET"],
        root="https://api.flair.co/",
        user_agent="BedroomAutomation/0.1.0",
    )

    rooms = client.get("rooms")
    bedroom = next(
        room for room in rooms
        if room.attributes.get("name") == "Bedroom"
    )

    temp_c = bedroom.attributes["current-temperature-c"]

    return {
        "temp": temp_c * 9 / 5 + 32,
        "humidity": bedroom.attributes.get("current-humidity"),
        "updated": bedroom.attributes.get("state-updated-at"),
    }


def get_outdoor():
    location = os.environ["WEATHER_LOCATION"]

    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={
            "name": location,
            "count": 1,
            "countryCode": "US",
        },
        timeout=10,
    )
    geo.raise_for_status()
    results = geo.json().get("results", [])
    if not results:
        raise ValueError(f"Location not found: {location}")
    place = results[0]

    weather = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": (
                "temperature_2m,"
                "relative_humidity_2m,"
                "dew_point_2m"
            ),
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
        },
        timeout=10,
    )
    weather.raise_for_status()

    current = weather.json()["current"]

    return {
        "temp": current["temperature_2m"],
        "humidity": current["relative_humidity_2m"],
        "dew_point": current["dew_point_2m"],
        "updated": current["time"],
    }


def evaluate(indoor, outdoor):
    if indoor <= TARGET_TEMP:
        return "STOP", "Bedroom reached target"

    if outdoor > indoor - MIN_OUTDOOR_DIFFERENCE:
        return "STOP", "Outside air isn't cool enough"

    if indoor >= RESTART_TEMP:
        return "START", "Bedroom warm; outside air is cooler"

    return "HOLD", "Maintain previous fan state"


if __name__ == "__main__":
    bedroom = get_bedroom()
    outside = get_outdoor()

    action, reason = evaluate(
        bedroom["temp"],
        outside["temp"],
    )

    print(f"Bedroom: {bedroom['temp']:.1f}°F")
    print(f"Outside: {outside['temp']:.1f}°F")
    print(f"Difference: {bedroom['temp'] - outside['temp']:.1f}°F")
    print(f"Outdoor dew point: {outside['dew_point']:.1f}°F")
    print(f"Bedroom data updated: {bedroom['updated']}")
    print(f"Weather observation: {outside['updated']}")
    print()
    print(f"Recommended action: {action}")
    print(f"Reason: {reason}")
