
import os
import requests
from dotenv import load_dotenv

load_dotenv()

location = os.environ["WEATHER_LOCATION"]

# Resolve city or ZIP code to coordinates
geo_response = requests.get(
    "https://geocoding-api.open-meteo.com/v1/search",
    params={
        "name": location,
        "count": 1,
        "countryCode": "US",
    },
    timeout=10,
)
geo_response.raise_for_status()

results = geo_response.json().get("results", [])
if not results:
    raise ValueError(f"Location not found: {location}")

place = results[0]

# Retrieve current outdoor conditions
weather_response = requests.get(
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
weather_response.raise_for_status()

current = weather_response.json()["current"]

print(f"Location: {place['name']}, {place.get('admin1', '')}")
print(f"Outdoor temperature: {current['temperature_2m']}°F")
print(f"Outdoor humidity: {current['relative_humidity_2m']}%")
print(f"Outdoor dew point: {current['dew_point_2m']}°F")
print(f"Weather timestamp: {current['time']}")
