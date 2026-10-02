from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
import requests

app = Flask(__name__)
CORS(app)

arg_tz = timezone(timedelta(hours=-3))

# Coordenadas de Córdoba Capital
LAT = -31.4201
LON = -64.1888

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&current=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_gusts_10m,weather_code"
    "&hourly=precipitation_probability"
    "&timezone=America%2FArgentina%2FCordoba&forecast_days=1"
)

# Códigos WMO (estándar meteorológico que usa Open-Meteo) relevantes para nuestro caso.
# Fuente: https://open-meteo.com/en/docs -> "WMO Weather interpretation codes"
WEATHER_CODE_MAP = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado", 3: "Nublado",
    45: "Niebla", 48: "Niebla con escarcha",
    51: "Llovizna débil", 53: "Llovizna moderada", 55: "Llovizna intensa",
    61: "Lluvia débil", 63: "Lluvia moderada", 65: "Lluvia intensa",
    71: "Nieve débil", 73: "Nieve moderada", 75: "Nieve intensa",
    80: "Chubascos débiles", 81: "Chubascos moderados", 82: "Chubascos intensos",
    95: "Tormenta", 96: "Tormenta con granizo leve", 99: "Tormenta con granizo fuerte",
}

HAIL_CODES = {96, 99}


@app.route('/')
def home():
    return "🤖 Bot meteorológico Córdoba - API activa."


@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    now = datetime.now(arg_tz)
    current_time_str = now.strftime("%d/%m/%Y %H:%M:%S")

    try:
        resp = requests.get(OPEN_METEO_URL, timeout=8)
        resp.raise_for_status()
        meteo = resp.json()

        current = meteo["current"]
        code = current.get("weather_code", 0)
        condition_text = WEATHER_CODE_MAP.get(code, "Condición desconocida")
        hail_possible = code in HAIL_CODES

        wind_gusts = current.get("wind_gusts_10m", 0) or 0
        precipitation = current.get("precipitation", 0) or 0

        # Heurística propia de severidad (NO es un aviso oficial del SMN,
        # es una estimación nuestra en base a datos reales del modelo).
        if hail_possible:
            alert_level = "naranja"
        elif wind_gusts >= 60 or precipitation >= 10:
            alert_level = "amarilla"
        else:
            alert_level = "ninguna"

        # Timeline real: próximas horas con probabilidad de precipitación (dato real, no inventado)
        hourly_times = meteo.get("hourly", {}).get("time", [])
        hourly_probs = meteo.get("hourly", {}).get("precipitation_probability", [])
        current_hour_str = now.strftime("%Y-%m-%dT%H:00")
        start_idx = hourly_times.index(current_hour_str) if current_hour_str in hourly_times else 0

        forecast_timeline = []
        for h in range(start_idx, min(start_idx + 4, len(hourly_times))):
            hour_label = hourly_times[h].split("T")[1]
            forecast_timeline.append({
                "hour": hour_label,
                "precip_probability": hourly_probs[h] if h < len(hourly_probs) else None
            })

        data = {
            "source": "Open-Meteo (datos reales, modelo meteorológico, no es un parte oficial del SMN)",
            "current_conditions": {
                "temperature_c": current.get("temperature_2m"),
                "humidity_pct": current.get("relative_humidity_2m"),
                "wind_speed_kmh": current.get("wind_speed_10m"),
                "wind_gusts_kmh": wind_gusts,
                "precipitation_mm": precipitation,
                "condition_text": condition_text,
                "condition_code": code,
            },
            "alert_level": alert_level,
            "hail_possible": hail_possible,
            "forecast_timeline": forecast_timeline,
            "smn_official_url": "https://www.smn.gob.ar/pronostico/cordoba",
            "last_update": current_time_str,
            "ok": True,
        }
    except Exception as e:
        # Si Open-Meteo falla o no responde, avisamos explícitamente en vez de
        # inventar datos para que la app "se vea bien".
        data = {
            "ok": False,
            "error": "No se pudo obtener el pronóstico en este momento.",
            "detail": str(e),
            "smn_official_url": "https://www.smn.gob.ar/pronostico/cordoba",
            "last_update": current_time_str,
        }

    return jsonify(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
