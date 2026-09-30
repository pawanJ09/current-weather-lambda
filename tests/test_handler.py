import json
from unittest.mock import MagicMock, patch

import pytest

from src import handler

SAMPLE_OPEN_METEO_RESPONSE = {
    "latitude": 40.71,
    "longitude": -74.01,
    "timezone": "America/New_York",
    "current": {
        "time": "2026-09-29T14:00",
        "temperature_2m": 68.5,
        "apparent_temperature": 67.0,
        "relative_humidity_2m": 55,
        "precipitation": 0.0,
        "wind_speed_10m": 8.2,
        "wind_direction_10m": 210,
        "weather_code": 1,
    },
}


def _mock_urlopen(json_body, status=200):
    mock_response = MagicMock()
    mock_response.status = status
    mock_response.read.return_value = json.dumps(json_body).encode("utf-8")
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = False
    return mock_response


class TestFetchCurrentWeather:
    @patch("src.handler.urllib.request.urlopen")
    def test_returns_parsed_weather(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(SAMPLE_OPEN_METEO_RESPONSE)

        result = handler.fetch_current_weather(40.71, -74.01)

        assert result["temperature_f"] == 68.5
        assert result["feels_like_f"] == 67.0
        assert result["timezone"] == "America/New_York"
        assert result["wind_direction_deg"] == 210

    @patch("src.handler.urllib.request.urlopen")
    def test_raises_on_missing_current_block(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"latitude": 1, "longitude": 2})

        with pytest.raises(handler.WeatherError):
            handler.fetch_current_weather(1, 2)

    @patch("src.handler.urllib.request.urlopen")
    def test_raises_on_non_200(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(SAMPLE_OPEN_METEO_RESPONSE, status=500)

        with pytest.raises(handler.WeatherError):
            handler.fetch_current_weather(1, 2)

    @patch("src.handler.urllib.request.urlopen")
    def test_raises_on_url_error(self, mock_urlopen):
        mock_urlopen.side_effect = handler.urllib.error.URLError("no network")

        with pytest.raises(handler.WeatherError):
            handler.fetch_current_weather(1, 2)


class TestLambdaHandlerDirectInvocation:
    @patch("src.handler.fetch_current_weather")
    def test_success_returns_200(self, mock_fetch):
        mock_fetch.return_value = {"temperature_f": 70}
        event = {"latitude": 40.71, "longitude": -74.01}

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert json.loads(result["body"])["temperature_f"] == 70

    def test_missing_params_returns_400(self):
        result = handler.lambda_handler({}, None)

        assert result["statusCode"] == 400
        assert "error" in json.loads(result["body"])

    @patch("src.handler.fetch_current_weather")
    def test_upstream_failure_returns_502(self, mock_fetch):
        mock_fetch.side_effect = handler.WeatherError("boom")
        event = {"latitude": 1, "longitude": 2}

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 502


class TestLambdaHandlerBedrockInvocation:
    def _event(self, params=None):
        return {
            "actionGroup": "CurrentWeatherActionGroup",
            "apiPath": "/current-weather",
            "httpMethod": "GET",
            "parameters": params
            or [
                {"name": "latitude", "type": "number", "value": "40.71"},
                {"name": "longitude", "type": "number", "value": "-74.01"},
            ],
        }

    @patch("src.handler.fetch_current_weather")
    def test_success_returns_200(self, mock_fetch):
        mock_fetch.return_value = {"temperature_f": 70}

        result = handler.lambda_handler(self._event(), None)

        assert result["messageVersion"] == "1.0"
        assert result["response"]["httpStatusCode"] == 200
        body = json.loads(result["response"]["responseBody"]["application/json"]["body"])
        assert body["temperature_f"] == 70

    def test_missing_params_returns_400(self):
        result = handler.lambda_handler(self._event(params=[]), None)

        assert result["response"]["httpStatusCode"] == 400

    @patch("src.handler.fetch_current_weather")
    def test_upstream_failure_returns_502(self, mock_fetch):
        mock_fetch.side_effect = handler.WeatherError("boom")

        result = handler.lambda_handler(self._event(), None)

        assert result["response"]["httpStatusCode"] == 502
