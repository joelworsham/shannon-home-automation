"""Manual Open-Meteo smoke test."""
from check_cooling import get_outdoor

weather = get_outdoor()
print(f"Location: {weather['location']}")
print(f"Outdoor temperature: {weather['temp']}°F")
print(f"Outdoor humidity: {weather['humidity']}%")
print(f"Outdoor dew point: {weather['dew_point']}°F")
print(f"Weather timestamp: {weather['updated']}")
