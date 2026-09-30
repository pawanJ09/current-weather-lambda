"""
current-weather-lambda

Deterministic tool Lambda for the Agentic Weather App (Style 1: Amazon Bedrock
Agents). Fetches current weather conditions from Open-Meteo for a given
latitude/longitude.

Supports two invocation shapes:
  1. Bedrock Agent Action Group event (has "actionGroup" + "apiPath")
  2. Direct/manual invocation (a flat dict with "latitude" / "longitude")

Only the Python standard library is used (urllib) so the deployment
package needs no dependency layer -- just this file zipped up.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 8

CURRENT_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
)


class WeatherError(Exception):
    """Raised when Open-Meteo can't be reached or returns an unusable payload."""


def fetch_current_weather(latitude: float, longitude: float) -> dict[str, Any]:
    """Call Open-Meteo's current-conditions endpoint and return a flat dict."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(CURRENT_FIELDS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                raise WeatherError(f"Open-Meteo returned HTTP {resp.status}")
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise WeatherError(f"Failed to reach Open-Meteo: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WeatherError(f"Open-Meteo returned invalid JSON: {exc}") from exc

    current = payload.get("current")
    if not current:
        raise WeatherError("Open-Meteo response is missing the 'current' block")

    return {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "timezone": payload.get("timezone"),
        "observation_time": current.get("time"),
        "temperature_f": current.get("temperature_2m"),
        "feels_like_f": current.get("apparent_temperature"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "precipitation_in": current.get("precipitation"),
        "wind_speed_mph": current.get("wind_speed_10m"),
        "wind_direction_deg": current.get("wind_direction_10m"),
        "weather_code": current.get("weather_code"),
    }


def _is_bedrock_agent_event(event: dict[str, Any]) -> bool:
    return "actionGroup" in event and "apiPath" in event


def _extract_params_from_bedrock_event(event: dict[str, Any]) -> dict[str, str]:
    """Pull flat name->value pairs out of a Bedrock Agent Action Group event.

    Bedrock puts query-string style params in `parameters` and POST-body
    style params under `requestBody.content.<media-type>.properties`. This
    action group only uses GET-style query params, but both are handled.
    """
    params: dict[str, str] = {}

    for item in event.get("parameters", []) or []:
        params[item["name"]] = item["value"]

    content = event.get("requestBody", {}).get("content", {})
    for body in content.values():
        for prop in body.get("properties", []) or []:
            params[prop["name"]] = prop["value"]

    return params


def _bedrock_response(
    event: dict[str, Any], http_status: int, body: dict[str, Any]
) -> dict[str, Any]:
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup"),
            "apiPath": event.get("apiPath"),
            "httpMethod": event.get("httpMethod"),
            "httpStatusCode": http_status,
            "responseBody": {"application/json": {"body": json.dumps(body)}},
        },
    }


def _plain_response(http_status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": http_status, "body": json.dumps(body)}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    is_bedrock = _is_bedrock_agent_event(event)

    try:
        if is_bedrock:
            params = _extract_params_from_bedrock_event(event)
            latitude = float(params["latitude"])
            longitude = float(params["longitude"])
        else:
            latitude = float(event["latitude"])
            longitude = float(event["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        error_body = {"error": f"Missing or invalid latitude/longitude: {exc}"}
        return (
            _bedrock_response(event, 400, error_body)
            if is_bedrock
            else _plain_response(400, error_body)
        )

    try:
        weather = fetch_current_weather(latitude, longitude)
    except WeatherError as exc:
        error_body = {"error": str(exc)}
        return (
            _bedrock_response(event, 502, error_body)
            if is_bedrock
            else _plain_response(502, error_body)
        )

    return _bedrock_response(event, 200, weather) if is_bedrock else _plain_response(200, weather)
