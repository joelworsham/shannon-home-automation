"""Validated Flair room temperature and Open-Meteo weather integrations.

Both services require internet access. No hardware operations occur here.
"""

import os

import requests
from dotenv import load_dotenv
from flair_client import make_client

load_dotenv()


def get_bedroom():
    client = make_client(
        client_id=os.environ["FLAIR_CLIENT_ID"],
        client_secret=os.environ["FLAIR_CLIENT_SECRET"],
        root="https://api.flair.co/",
        user_agent="BedroomAutomation/0.1.0",
    )
    rooms = client.get("rooms")
    bedroom = next(
        (room for room in rooms if room.attributes.get("name") == "Bedroom"),
        None,
    )
    if bedroom is None:
        raise ValueError("Bedroom room not found in Flair account")
    celsius = bedroom.attributes.get("current-temperature-c")
    if celsius is None:
        raise ValueError("Flair Bedroom room has no current temperature")
    return {
        "temp": float(celsius) * 9 / 5 + 32,
        "humidity": bedroom.attributes.get("current-humidity"),
        # Flair state-updated-at has NOT been validated as the sensor-read time.
        "updated": bedroom.attributes.get("state-updated-at"),
    }


def get_outdoor():
    location = os.environ["WEATHER_LOCATION"]
    response = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1, "countryCode": "US"},
        timeout=10,
    )
    response.raise_for_status()
    matches = response.json().get("results", [])
    if not matches:
        raise ValueError(f"Open-Meteo location not found: {location}")
    place = matches[0]
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,dew_point_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
        },
        timeout=10,
    )
    response.raise_for_status()
    current = response.json()["current"]
    return {
        "temp": float(current["temperature_2m"]),
        "humidity": current.get("relative_humidity_2m"),
        "dew_point": current.get("dew_point_2m"),
        "updated": current.get("time"),
        "location": f"{place['name']}, {place.get('admin1', '')}",
    }


if __name__ == "__main__":
    from datetime import datetime
    from climate_policy import TIMEZONE, evaluate

    bedroom, outdoor = get_bedroom(), get_outdoor()
    decision = evaluate(
        when=datetime.now(TIMEZONE),
        bedroom_f=bedroom["temp"],
        outdoor_f=outdoor["temp"],
    )
    print(f"Bedroom: {bedroom['temp']:.1f}°F; humidity: {bedroom['humidity']}%")
    print(f"Outside ({outdoor['location']}): {outdoor['temp']:.1f}°F")
    print(f"Outdoor dew point: {outdoor['dew_point']}°F")
    print(f"Initial desired state (assuming previously off): {decision.desired}")
    print(f"Reason: {decision.reason}")
