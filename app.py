from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import math
import time
import requests
from PIL import Image

app = Flask(__name__)
CORS(app)

arg_tz = timezone(timedelta(hours=-3))

# Coordenadas de Córdoba Capital
LAT = -31.4201
LON = -64.1888

# ============================================================
# CACHÉ simple en memoria (evita golpear Open-Meteo/RainViewer en cada
# pedido; además de más rápido, esto es lo que evita pegarle al límite de
# rate-limit gratuito de Open-Meteo cuando hay varios clientes pidiendo
# a la vez o se recarga la página seguido).
# ============================================================
_weather_cache = {"data": None, "ts": 0, "ok": None}
_weather_last_good = {"data": None, "ts": 0}  # último pronóstico que SÍ funcionó
_trajectory_cache = {"data": None, "ts": 0}
CACHE_TTL_WEATHER_OK = 180     # 3 min: si el último pedido salió bien, no reconsultamos antes de esto
CACHE_TTL_WEATHER_ERROR = 20   # 20 s: si falló (ej. 429), reintentamos pronto en vez de quedarnos
                               # pegados al error por 3 min enteros
CACHE_TTL_TRAJECTORY = 300    # 5 min (coincide con el refresco del frontend)

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


# ============================================================
# TRAYECTORIA Y ETA DE TORMENTA (radar real, cálculo propio)
# ============================================================
# Idea: bajamos una grilla de 3x3 tiles del radar de RainViewer alrededor de
# Córdoba (cobertura real ~800km, probada para que Traslasierra y Sierras
# Chicas entren con margen), para los últimos frames disponibles (cada 10
# min). En cada frame calculamos el "centroide" de precipitación (el punto
# ponderado por intensidad de color/opacidad del radar). Comparando el
# centroide entre frames sacamos velocidad y rumbo, y extrapolamos un ETA.
#
# Esto es una ESTIMACIÓN propia (nowcasting lineal simple), no un dato
# oficial. Se degrada rápido más allá de ~60 min porque las tormentas
# cambian de velocidad/dirección.

RAINVIEWER_INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json"
RADAR_ZOOM = 7
TILE_SIZE = 256
FRAMES_A_USAR = 4  # últimos 4 frames = últimos 40 minutos

ZONAS_REFERENCIA = [
    ("Traslasierra", -31.72, -65.00),
    ("Sierras Chicas", -31.28, -64.33),
    ("Córdoba Capital", LAT, LON),
]

RUMBOS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
          "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def _deg2num_frac(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _deg2tile(lat_deg, lon_deg, zoom):
    x, y = _deg2num_frac(lat_deg, lon_deg, zoom)
    return int(x), int(y)


def _tile_pixel_to_latlon(xtile_base, ytile_base, zoom, px, py, tile_size=TILE_SIZE):
    n = 2.0 ** zoom
    x = xtile_base + px / tile_size
    y = ytile_base + py / tile_size
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _bearing_a_rumbo(brng):
    ix = round(brng / 22.5) % 16
    return RUMBOS[ix]


def _zona_mas_cercana(lat, lon):
    return min(ZONAS_REFERENCIA, key=lambda z: _haversine_km(lat, lon, z[1], z[2]))[0]


def _descargar_tile(url):
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


def _descargar_frame_compuesto(host, frame_path, xtile_base, ytile_base):
    """Baja la grilla de 3x3 tiles de un frame y las pega en una sola imagen.
    Devuelve (imagen_compuesta, tiles_descargados_ok) para poder diagnosticar
    si un resultado vacío es por falta de lluvia o porque las descargas
    están fallando."""
    composite = Image.new("RGBA", (TILE_SIZE * 3, TILE_SIZE * 3), (0, 0, 0, 0))
    tareas = {}
    tiles_ok = 0
    with ThreadPoolExecutor(max_workers=9) as pool:
        for dy in range(3):
            for dx in range(3):
                xt = xtile_base + dx
                yt = ytile_base + dy
                url = f"{host}{frame_path}/{TILE_SIZE}/{RADAR_ZOOM}/{xt}/{yt}/2/1_1.png"
                tareas[pool.submit(_descargar_tile, url)] = (dx, dy)
        for future in as_completed(tareas):
            dx, dy = tareas[future]
            try:
                tile_img = future.result()
                composite.paste(tile_img, (dx * TILE_SIZE, dy * TILE_SIZE))
                tiles_ok += 1
            except Exception:
                pass  # si falta un tile, seguimos con lo que tengamos
    return composite, tiles_ok


def _centroide_precipitacion(img):
    """Centroide ponderado por el canal alfa (opacidad = proxy de intensidad del radar)."""
    pixels = img.load()
    w, h = img.size
    total_peso = 0.0
    sum_x = 0.0
    sum_y = 0.0
    for y in range(h):
        for x in range(w):
            a = pixels[x, y][3]
            if a > 10:  # ignorar casi-transparente (ruido)
                total_peso += a
                sum_x += x * a
                sum_y += y * a
    if total_peso == 0:
        return None
    return (sum_x / total_peso, sum_y / total_peso, total_peso)


def analizar_trayectoria_tormenta():
    try:
        idx_resp = requests.get(RAINVIEWER_INDEX_URL, timeout=8)
        idx_resp.raise_for_status()
        idx = idx_resp.json()
        host = idx["host"]
        frames = idx.get("radar", {}).get("past", [])
        if len(frames) < 2:
            return {"ok": False, "error": "No hay suficientes frames de radar todavía."}

        frames_a_usar = frames[-FRAMES_A_USAR:] if len(frames) >= FRAMES_A_USAR else frames

        xt0, yt0 = _deg2tile(LAT, LON, RADAR_ZOOM)
        xtile_base, ytile_base = xt0 - 1, yt0 - 1  # esquina de la grilla 3x3

        # Diagnóstico: para poder distinguir "no está lloviendo" de "algo se
        # rompió" cuando no se puede calcular trayectoria.
        tiles_ok_total = 0
        tiles_esperados_total = len(frames_a_usar) * 9

        puntos = []
        for f in frames_a_usar:
            composite, tiles_ok = _descargar_frame_compuesto(host, f["path"], xtile_base, ytile_base)
            tiles_ok_total += tiles_ok
            centroide = _centroide_precipitacion(composite)
            if centroide is None:
                continue
            px, py, peso = centroide
            lat, lon = _tile_pixel_to_latlon(xtile_base, ytile_base, RADAR_ZOOM, px, py)
            puntos.append({"time": f["time"], "lat": lat, "lon": lon, "peso": peso})

        diagnostico = {
            "frames_en_indice_rainviewer": len(frames),
            "frames_evaluados": len(frames_a_usar),
            "frames_con_senal_de_lluvia": len(puntos),
            "tiles_descargados_ok": f"{tiles_ok_total}/{tiles_esperados_total}",
        }

        if len(puntos) < 2:
            if tiles_ok_total == 0:
                motivo = "No se pudo descargar ningún tile del radar (posible problema de red o de la URL del host de RainViewer, no falta de lluvia)."
            elif tiles_ok_total < tiles_esperados_total:
                motivo = "Se descargaron algunos tiles pero no todos; puede haber afectado la detección. Puede ser un problema transitorio de red."
            else:
                motivo = "Las descargas funcionaron bien (todos los tiles OK); simplemente no hay precipitación detectable en el radar en esta zona ahora mismo."
            return {
                "ok": False,
                "error": "No se detectó suficiente precipitación en el radar en la zona para calcular trayectoria.",
                "motivo_probable": motivo,
                "diagnostico": diagnostico,
            }

        primero, ultimo = puntos[0], puntos[-1]
        dt_min = (ultimo["time"] - primero["time"]) / 60.0
        if dt_min <= 0:
            return {"ok": False, "error": "Datos de tiempo inconsistentes del radar."}

        dist_recorrida_km = _haversine_km(primero["lat"], primero["lon"], ultimo["lat"], ultimo["lon"])
        velocidad_kmh = (dist_recorrida_km / dt_min) * 60
        rumbo = _bearing_deg(primero["lat"], primero["lon"], ultimo["lat"], ultimo["lon"])
        dist_a_capital_km = _haversine_km(ultimo["lat"], ultimo["lon"], LAT, LON)

        if velocidad_kmh < 2:
            eta = {"eta_minutos": None, "nota": "El sistema se mueve muy poco o está estacionario; no se puede estimar una hora de llegada confiable."}
        else:
            eta = {"eta_minutos": round((dist_a_capital_km / velocidad_kmh) * 60), "nota": None}

        return {
            "ok": True,
            "zona_actual_aproximada": _zona_mas_cercana(ultimo["lat"], ultimo["lon"]),
            "distancia_a_capital_km": round(dist_a_capital_km, 1),
            "velocidad_estim_kmh": round(velocidad_kmh, 1),
            "rumbo_grados": round(rumbo),
            "rumbo_compass": _bearing_a_rumbo(rumbo),
            "eta": eta,
            "frames_usados": len(puntos),
            "metodo": "Centroide ponderado de reflectividad en radar RainViewer, comparado entre frames (nowcasting lineal). Estimación propia, no oficial.",
            "diagnostico": diagnostico,
        }
    except Exception as e:
        return {"ok": False, "error": f"No se pudo calcular la trayectoria: {str(e)}"}


@app.route('/api/storm-trajectory', methods=['GET'])
def storm_trajectory():
    now = time.time()
    if _trajectory_cache["data"] is not None and (now - _trajectory_cache["ts"]) < CACHE_TTL_TRAJECTORY:
        return jsonify(_trajectory_cache["data"])

    resultado = analizar_trayectoria_tormenta()
    _trajectory_cache["data"] = resultado
    _trajectory_cache["ts"] = now
    return jsonify(resultado)


@app.route('/')
def home():
    return "🤖 Bot meteorológico Córdoba - API activa."


def _fetch_open_meteo_con_reintento():
    """Un pedido a Open-Meteo, con un reintento corto si falla (sobre todo
    pensado para un 429 pasajero por compartir IP de salida en Render)."""
    try:
        resp = requests.get(OPEN_METEO_URL, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        time.sleep(1.5)
        resp = requests.get(OPEN_METEO_URL, timeout=8)
        resp.raise_for_status()
        return resp.json()


@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    now_ts = time.time()
    cache_ttl = CACHE_TTL_WEATHER_OK if _weather_cache.get("ok") else CACHE_TTL_WEATHER_ERROR
    if _weather_cache["data"] is not None and (now_ts - _weather_cache["ts"]) < cache_ttl:
        return jsonify(_weather_cache["data"])

    now = datetime.now(arg_tz)
    current_time_str = now.strftime("%d/%m/%Y %H:%M:%S")

    try:
        meteo = _fetch_open_meteo_con_reintento()

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
            "stale": False,
        }
        # Éxito: lo guardamos como el último dato bueno, para poder usarlo
        # si el próximo pedido falla (ej. 429 por IP compartida en Render).
        _weather_last_good["data"] = data
        _weather_last_good["ts"] = now_ts
        _weather_cache["ok"] = True

    except Exception as e:
        # Si Open-Meteo falla (ej. límite de la IP compartida de Render, no
        # necesariamente nuestro propio tráfico), preferimos mostrar el
        # último dato real que sí tuvimos, marcado como desactualizado, en
        # vez de dejar la pantalla vacía. Solo si nunca tuvimos ningún dato
        # bueno mostramos el error explícito.
        if _weather_last_good["data"] is not None:
            data = dict(_weather_last_good["data"])
            edad_min = round((now_ts - _weather_last_good["ts"]) / 60, 1)
            data["stale"] = True
            data["stale_minutes"] = edad_min
            data["stale_reason"] = "No se pudo actualizar ahora (probable límite temporal de la fuente de datos); mostrando el último dato real obtenido."
        else:
            data = {
                "ok": False,
                "error": "No se pudo obtener el pronóstico en este momento.",
                "detail": str(e),
                "smn_official_url": "https://www.smn.gob.ar/pronostico/cordoba",
                "last_update": current_time_str,
            }
        _weather_cache["ok"] = False

    _weather_cache["data"] = data
    _weather_cache["ts"] = now_ts

    return jsonify(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
