from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
import time
import os
import threading

# Importamos nuestros agentes especializados
from agentes import (
    AgenteMeteorologo,
    AgenteMeteorologoRespaldo,
    AgenteTermodinamicoGranizo,
    AgenteGeoespacialETA,
    AgenteConversacional,
)

app = Flask(__name__)
CORS(app)

arg_tz = timezone(timedelta(hours=-3))
LAT = -31.4201
LON = -64.1888

# Instanciamos los agentes con sus configuraciones base
agente_meteo = AgenteMeteorologo(LAT, LON)
agente_meteo_respaldo = AgenteMeteorologoRespaldo(LAT, LON)
agente_granizo = AgenteTermodinamicoGranizo()
agente_chat = AgenteConversacional()

DESTINOS_CAPITAL = [
    ("Centro", LAT, LON),
    ("Zona Norte (Cerro de las Rosas / Villa Belgrano)", -31.3765, -64.2356),
    ("Zona Sur (Villa El Libertador / Juniors)", -31.4762, -64.1739),
    ("Zona Oeste (Argüello)", -31.3886, -64.2653),
    ("Zona Este (Barrio Jardín / Gral. Paz)", -31.4075, -64.1578),
]
agente_geo = AgenteGeoespacialETA(DESTINOS_CAPITAL)

# Cachés simples en memoria
_weather_cache = {"data": None, "ts": 0, "ok": None}
_trajectory_cache = {"data": None, "ts": 0, "ok": None}
_weather_last_good = {"data": None, "ts": 0}
CACHE_TTL_WEATHER_OK = 180
_refresh_lock = threading.Lock()
_started_ts = time.time()

WEATHER_CODE_MAP = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado", 3: "Nublado",
    45: "Niebla", 48: "Niebla con escarcha", 51: "Llovizna débil", 53: "Llovizna moderada",
    55: "Llovizna intensa", 61: "Lluvia débil", 63: "Lluvia moderada", 65: "Lluvia intensa",
    80: "Chubascos débiles", 81: "Chubascos moderados", 82: "Chubascos intensos",
    95: "Tormenta", 96: "Tormenta con granizo leve", 99: "Tormenta con granizo fuerte",
}
HAIL_CODES = {96, 99}

def evaluar_formacion_tormenta(forecast_timeline, indice_granizo):
    """Evalúa formación de tormenta de forma más estricta para evitar falsos positivos."""
    probs = [p.get("precip_probability") for p in (forecast_timeline or [])]
    probs = [p for p in probs if isinstance(p, (int, float))]
    altas = [p for p in probs if p >= 75]  # Subimos exigencia a 75%
    
    # Solo alerta por formación si hay alta probabilidad Y el agente detecta algo de inestabilidad (score >= 15)
    if len(altas) >= 2 and indice_granizo.get("score", 0) >= 15:
        return True, f"El pronóstico indica {max(altas):.0f}% de probabilidad de precipitación con inestabilidad atmosférica."
    return False, None


def _armar_datos_con_agentes(meteo_data, now, current_time_str):
    """El orquestador coordina la salida de los agentes."""
    current = meteo_data["current"]
    code = current.get("weather_code", 0)
    condition_text = WEATHER_CODE_MAP.get(code, "Condición desconocida")
    hail_possible = code in HAIL_CODES

    wind_speed = current.get("wind_speed_10m", 0) or 0
    wind_gusts = current.get("wind_gusts_10m", 0) or 0
    precipitation = current.get("precipitation", 0) or 0

    hourly = meteo_data.get("hourly", {})
    hourly_times = hourly.get("time", [])
    hourly_probs = hourly.get("precipitation_probability", [])
    hourly_cape = hourly.get("cape", [])
    hourly_freezing = hourly.get("freezing_level_height", [])
    hourly_w80 = hourly.get("wind_speed_80m", [])

    current_hour_str = now.strftime("%Y-%m-%dT%H:00")
    start_idx = hourly_times.index(current_hour_str) if current_hour_str in hourly_times else 0

    cape_now = hourly_cape[start_idx] if start_idx < len(hourly_cape) else None
    freezing_now = hourly_freezing[start_idx] if start_idx < len(hourly_freezing) else None
    wind_80_now = hourly_w80[start_idx] if start_idx < len(hourly_w80) else None

    # Primero evaluamos el índice de granizo/inestabilidad
    indice_granizo = agente_granizo.evaluar_riesgo(
        cape_now, freezing_now, code, wind_gusts, wind_speed, wind_80_now
    )

    forecast_timeline = []
    for h in range(start_idx, min(start_idx + 4, len(hourly_times))):
        hour_label = hourly_times[h].split("T")[1]
        forecast_timeline.append({
            "hour": hour_label,
            "precip_probability": hourly_probs[h] if h < len(hourly_probs) else None
        })

    # Evaluamos formación de tormenta pasando el índice calculado
    storm_forming, storm_forming_detail = evaluar_formacion_tormenta(forecast_timeline, indice_granizo)

    if indice_granizo["nivel"] == "Alto":
        alert_level = "naranja"
    elif indice_granizo["nivel"] == "Moderado" or wind_gusts >= 55 or precipitation >= 10:
        alert_level = "amarilla"
    elif storm_forming:
        alert_level = "amarilla"
    else:
        alert_level = "ninguna"

    return {
        "source": "Sistema Multi-Agente Avanzado (Open-Meteo + Cizalladura + Radar)",
        "source_id": "open-meteo-agentes-avanzados",
        "current_conditions": {
            "temperature_c": current.get("temperature_2m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "wind_speed_kmh": wind_speed,
            "wind_gusts_kmh": wind_gusts,
            "precipitation_mm": precipitation,
            "condition_text": condition_text,
            "condition_code": code,
            "cape": cape_now,
            "freezing_level_m": freezing_now,
        },
        "hail_index": indice_granizo,
        "alert_level": alert_level,
        "hail_possible": hail_possible or indice_granizo["score"] >= 30, # Ajustado para evitar falsos positivos menores
        "storm_forming": storm_forming,
        "storm_forming_detail": storm_forming_detail,
        "forecast_timeline": forecast_timeline,
        "degraded": False,
        "smn_official_url": "https://www.smn.gob.ar/pronostico/cordoba",
        "last_update": current_time_str,
        "ok": True,
        "stale": False,
    }


@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    now_ts = time.time()
    if _weather_cache["data"] is not None and (now_ts - _weather_cache["ts"]) < CACHE_TTL_WEATHER_OK:
        return jsonify(_weather_cache["data"])
    
    try:
        raw_data = agente_meteo.obtener_estado_actual()
        now = datetime.now(arg_tz)
        current_str = now.strftime("%d/%m/%Y %H:%M:%S")
        
        resultado = _armar_datos_con_agentes(raw_data, now, current_str)
        _weather_cache["data"] = resultado
        _weather_cache["ts"] = now_ts
        _weather_cache["ok"] = True
        _weather_last_good["data"] = resultado
        _weather_last_good["ts"] = now_ts
        return jsonify(resultado)
    except Exception as e:
        if _weather_last_good["data"]:
            return jsonify(_weather_last_good["data"])
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/storm-trajectory', methods=['GET'])
def storm_trajectory():
    """Endpoint simplificado de trayectoria y ETAs para el radar."""
    try:
        velocidad_estimada = 35  # km/h
        rumbo_grados = 135       # SE
        etas = agente_geo.calcular_etas(LAT, LON, velocidad_estimada)
        
        trajectory_data = {
            "ok": True,
            "zona_actual_aproximada": "Sierras Chicas / Noroeste",
            "rumbo_grados": rumbo_grados,
            "rumbo_compass": "Sudeste (SE)",
            "velocidad_estim_kmh": velocidad_estimada,
            "etas_por_zona": etas
        }
        _trajectory_cache["data"] = trajectory_data
        _trajectory_cache["ts"] = time.time()
        return jsonify(trajectory_data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat_clima():
    """Endpoint del Agente Conversacional para procesar preguntas desde la web."""
    if request.method == 'OPTIONS':
        return jsonify({"ok": True}), 200
        
    body = request.get_json() or {}
    pregunta = body.get("pregunta", "")
    
    clima_actual = _weather_cache.get("data") or {}
    trayectoria_actual = _trajectory_cache.get("data") or {}
    
    respuesta = agente_chat.responder_consulta(pregunta, clima_actual, trayectoria_actual)
    return jsonify({"respuesta": respuesta})


@app.route('/')
def home():
    return "🤖 Sistema Multi-Agente Avanzado de Clima Córdoba - API activa."


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
