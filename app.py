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
    "&hourly=precipitation_probability,cape,freezing_level_height"
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


def calcular_indice_granizo(cape, freezing_level_m, weather_code, wind_gusts_kmh):
    """
    Índice propio de riesgo de granizo (0-100), NO es un pronóstico oficial.
    Combina 4 ingredientes reales que un meteorólogo mira para esto:

    - CAPE (energía convectiva disponible): más energía = corrientes ascendentes
      más fuertes = más capacidad de sostener piedras de granizo mientras crecen.
    - Altura del nivel de congelamiento (0°C): cuanto más bajo, menos distancia
      recorre el granizo en aire cálido antes de tocar el piso, así que llega
      más grande e intacto.
    - Código de condición del modelo (96/99 = tormenta con granizo según Open-Meteo).
    - Ráfagas de viento: acompañan a las tormentas más severas.

    Los umbrales de CAPE y nivel de congelamiento son referencias generales de
    meteorología convectiva, no una fórmula oficial de ningún servicio.
    """
    score = 0
    detail = []

    if cape is not None:
        if cape >= 4000:
            score += 40
            detail.append(f"CAPE muy alto ({cape:.0f} J/kg)")
        elif cape >= 2500:
            score += 30
            detail.append(f"CAPE alto ({cape:.0f} J/kg)")
        elif cape >= 1000:
            score += 15
            detail.append(f"CAPE moderado ({cape:.0f} J/kg)")
        elif cape >= 300:
            score += 5
            detail.append(f"CAPE bajo ({cape:.0f} J/kg)")

    if freezing_level_m is not None:
        if freezing_level_m <= 3000:
            score += 25
            detail.append(f"nivel de congelamiento bajo ({freezing_level_m:.0f} m)")
        elif freezing_level_m <= 4000:
            score += 12
            detail.append(f"nivel de congelamiento moderado ({freezing_level_m:.0f} m)")

    if weather_code in HAIL_CODES:
        score += 25
        detail.append("modelo indica tormenta con granizo")
    elif weather_code == 95:
        score += 10
        detail.append("modelo indica tormenta")

    if wind_gusts_kmh is not None:
        if wind_gusts_kmh >= 70:
            score += 10
            detail.append(f"ráfagas fuertes ({wind_gusts_kmh:.0f} km/h)")
        elif wind_gusts_kmh >= 50:
            score += 5
            detail.append(f"ráfagas moderadas ({wind_gusts_kmh:.0f} km/h)")

    score = min(score, 100)

    if score >= 60:
        nivel = "Alto"
    elif score >= 35:
        nivel = "Moderado"
    elif score >= 15:
        nivel = "Bajo"
    else:
        nivel = "Muy bajo"

    return {
        "score": score,
        "nivel": nivel,
        "factores": detail,
    }


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

        # Buscamos CAPE y nivel de congelamiento de la hora actual en el bloque hourly
        hourly_times = meteo.get("hourly", {}).get("time", [])
        hourly_probs = meteo.get("hourly", {}).get("precipitation_probability", [])
        hourly_cape = meteo.get("hourly", {}).get("cape", [])
        hourly_freezing = meteo.get("hourly", {}).get("freezing_level_height", [])

        current_hour_str = now.strftime("%Y-%m-%dT%H:00")
        start_idx = hourly_times.index(current_hour_str) if current_hour_str in hourly_times else 0

        cape_now = hourly_cape[start_idx] if start_idx < len(hourly_cape) else None
        freezing_now = hourly_freezing[start_idx] if start_idx < len(hourly_freezing) else None

        indice_granizo = calcular_indice_granizo(cape_now, freezing_now, code, wind_gusts)

        # Heurística de severidad general (combina el índice de granizo con lluvia/viento)
        if indice_granizo["nivel"] == "Alto":
            alert_level = "naranja"
        elif indice_granizo["nivel"] == "Moderado" or wind_gusts >= 60 or precipitation >= 10:
            alert_level = "amarilla"
        else:
            alert_level = "ninguna"

        # Timeline real: próximas horas con probabilidad de precipitación (dato real)
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
                "cape": cape_now,
                "freezing_level_m": freezing_now,
            },
            "hail_index": indice_granizo,
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
