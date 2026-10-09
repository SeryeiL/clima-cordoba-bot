from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
from io import BytesIO
import math
import os
import threading
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
CACHE_TTL_TRAJECTORY = 300     # 5 min (el radar de OHMC se actualiza cada ~9-10 min)

# Evita que varios pedidos simultáneos (ej. el servidor recién despertó y
# llegan 5 clientes juntos) disparen cada uno su propio pedido a las fuentes.
_refresh_lock = threading.Lock()

_started_ts = time.time()

# ============================================================
# CIRCUIT BREAKER para Open-Meteo
# ============================================================
# Si Open-Meteo responde 429 (rate limit por IP compartida de Render), seguir
# insistiendo en cada pedido solo suma latencia (reintento + espera) y empeora
# el límite. En vez de eso, "abrimos el breaker": durante un rato salteamos
# Open-Meteo y vamos directo a la fuente de respaldo. Pasado el tiempo,
# probamos de nuevo una vez (si funciona, el breaker se cierra).
BREAKER_COOLDOWN_429 = 600     # 10 min tras un 429 (si el servidor no indica Retry-After)
BREAKER_COOLDOWN_ERROR = 120   # 2 min tras otro tipo de error (timeout, 5xx, JSON roto)
BREAKER_MIN = 60               # nunca menos de 1 min
BREAKER_MAX = 3600             # nunca más de 1 h (por si Retry-After viene exagerado)
_breaker = {"open_until": 0.0, "motivo": None}

# Estadísticas por fuente, solo para /api/health (diagnóstico).
def _nuevas_stats():
    return {"ok_count": 0, "error_count": 0, "last_ok_ts": None, "last_error_ts": None, "last_error": None}

_source_stats = {"open-meteo": _nuevas_stats(), "metno": _nuevas_stats()}

# Si el dato bueno más reciente tiene más de esto, el estado pasa a "desactualizado".
HEALTH_MAX_AGE_OK = 900  # 15 min

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

# Umbral y cantidad mínima de horas consecutivas con alta probabilidad de
# precipitación para considerar que "se está formando algo", aunque todavía
# no haya lluvia/CAPE/granizo en el instante actual.
FORMACION_PROB_UMBRAL = 60
FORMACION_HORAS_MIN = 2


def evaluar_formacion_tormenta(forecast_timeline):
    """
    Mira el PRONÓSTICO de probabilidad de precipitación de las próximas horas
    (dato real, no el radar) para detectar que algo se está formando, incluso
    cuando las condiciones actuales todavía no lo muestran (sin lluvia ni CAPE
    alto en este instante, o en modo reducido sin CAPE disponible).

    Esto es intencionalmente independiente del índice de granizo: el índice de
    granizo mira el AHORA, esto mira las próximas horas. Sirve para no
    depender únicamente del radar (RainViewer) para avisar de una tormenta que
    recién está empezando.
    """
    probs = [p.get("precip_probability") for p in (forecast_timeline or [])]
    probs = [p for p in probs if isinstance(p, (int, float))]
    altas = [p for p in probs if p >= FORMACION_PROB_UMBRAL]
    if len(altas) >= FORMACION_HORAS_MIN:
        return True, (
            f"El pronóstico indica {max(altas):.0f}% de probabilidad de precipitación "
            f"sostenida en las próximas horas, aunque el dato instantáneo todavía no lo muestre."
        )
    return False, None


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
# FUENTE DE RESPALDO: MET Norway (gratis, sin API key)
# ============================================================
# Open-Meteo viene devolviendo 429 de forma persistente, consistente con que
# el plan free de Render comparte IP de salida con otros proyectos y ESA ip
# ya está rate-limiteada en Open-Meteo (no necesariamente por nuestro propio
# tráfico). En vez de depender de un solo proveedor, agregamos una segunda
# fuente completamente independiente (otro servidor, otro esquema de límites)
# para cuando la primera no responda. MET Norway no tiene CAPE ni nivel de
# congelamiento, así que el índice de granizo pasa a un "modo reducido"
# explícito, menos preciso pero mejor que no mostrar nada.

METNO_URL = (
    "https://api.met.no/weatherapi/locationforecast/2.0/compact"
    f"?lat={LAT}&lon={LON}"
)
# MET Norway pide identificar la app en el User-Agent (no hace falta API key).
METNO_HEADERS = {
    "User-Agent": "ClimaCordobaPro/1.0 github.com/seryeil/clima-cordoba-bot"
}

METNO_SYMBOL_MAP = {
    "clearsky": "Despejado",
    "fair": "Mayormente despejado",
    "partlycloudy": "Parcialmente nublado",
    "cloudy": "Nublado",
    "fog": "Niebla",
    "lightrainshowers": "Chubascos débiles",
    "rainshowers": "Chubascos moderados",
    "heavyrainshowers": "Chubascos intensos",
    "lightrain": "Lluvia débil",
    "rain": "Lluvia moderada",
    "heavyrain": "Lluvia intensa",
    "lightsleet": "Aguanieve débil",
    "sleet": "Aguanieve",
    "lightsnow": "Nieve débil",
    "snow": "Nieve moderada",
    "heavysnow": "Nieve intensa",
    "thunderstorm": "Tormenta",
    "lightrainshowersandthunder": "Chubascos con tormenta",
    "rainshowersandthunder": "Chubascos con tormenta",
    "heavyrainshowersandthunder": "Chubascos intensos con tormenta",
    "rainandthunder": "Lluvia con tormenta",
    "heavyrainandthunder": "Lluvia intensa con tormenta",
    "lightssleetshowersandthunder": "Aguanieve con tormenta",
    "lightsnowshowersandthunder": "Nieve débil con tormenta",
}


def _metno_symbol_a_texto(symbol_code):
    base = (symbol_code or "").split("_")[0]  # saca sufijo _day/_night/_polartwilight
    return METNO_SYMBOL_MAP.get(base, "Condición desconocida (respaldo MET Norway)")


def _metno_tiene_tormenta(symbol_code):
    return "thunder" in (symbol_code or "")


def calcular_indice_granizo_reducido(symbol_code, wind_gusts_kmh):
    """
    Versión reducida del índice de granizo, usada solo cuando la fuente
    principal (Open-Meteo) falló y estamos usando el respaldo MET Norway,
    que no da CAPE ni nivel de congelamiento. El puntaje máximo alcanzable
    es más bajo a propósito, y siempre se marca como "modo reducido" para
    no confundirlo con el índice completo.
    """
    score = 0
    detail = ["Modo reducido (respaldo MET Norway): sin CAPE ni nivel de congelamiento disponibles"]

    if _metno_tiene_tormenta(symbol_code):
        score += 40
        detail.append("modelo de respaldo indica tormenta eléctrica")

    if wind_gusts_kmh is not None:
        if wind_gusts_kmh >= 70:
            score += 15
            detail.append(f"ráfagas fuertes ({wind_gusts_kmh:.0f} km/h)")
        elif wind_gusts_kmh >= 50:
            score += 8
            detail.append(f"ráfagas moderadas ({wind_gusts_kmh:.0f} km/h)")

    score = min(score, 100)
    if score >= 40:
        nivel = "Moderado"
    elif score >= 15:
        nivel = "Bajo"
    else:
        nivel = "Muy bajo"

    return {"score": score, "nivel": nivel, "factores": detail}


def _fetch_metno():
    """Pedido a la fuente de respaldo. Tira excepción si también falla,
    igual que el pedido principal, para que el llamador decida qué hacer."""
    resp = requests.get(METNO_URL, headers=METNO_HEADERS, timeout=8)
    resp.raise_for_status()
    body = resp.json()
    series = body["properties"]["timeseries"]
    ahora = series[0]
    details = ahora["data"]["instant"]["details"]

    next1 = ahora["data"].get("next_1_hours", {})
    symbol_code = next1.get("summary", {}).get("symbol_code", "")
    precip_1h = next1.get("details", {}).get("precipitation_amount")

    wind_speed_ms = details.get("wind_speed")
    wind_speed_kmh = wind_speed_ms * 3.6 if wind_speed_ms is not None else None
    # El endpoint "compact" de MET Norway no siempre trae ráfaga; si no está,
    # queda en None (no inventamos un valor) y el índice reducido lo nota.
    wind_gust_ms = details.get("wind_speed_of_gust")
    wind_gust_kmh = wind_gust_ms * 3.6 if wind_gust_ms is not None else None

    condition_text = _metno_symbol_a_texto(symbol_code)
    hail_possible = _metno_tiene_tormenta(symbol_code)
    indice_granizo = calcular_indice_granizo_reducido(symbol_code, wind_gust_kmh)

    forecast_timeline = []
    for entry in series[1:5]:
        t = entry["time"]  # ISO UTC, ej "2026-10-06T12:00:00Z"
        try:
            hora_utc = datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            hora_local = hora_utc.astimezone(arg_tz)
            hour_label = hora_local.strftime("%H:%M")
        except Exception:
            hour_label = "--:--"
        n1 = entry["data"].get("next_1_hours", {})
        precip_amt = n1.get("details", {}).get("precipitation_amount")
        # MET Norway (compact) no da probabilidad directa de precipitación;
        # usamos la cantidad prevista como señal aproximada (es una
        # estimación nuestra, no el dato crudo de probabilidad de Open-Meteo).
        prob_aprox = 70 if (precip_amt is not None and precip_amt > 0) else 0
        forecast_timeline.append({
            "hour": hour_label,
            "precip_probability": prob_aprox,
        })

    storm_forming, storm_forming_detail = evaluar_formacion_tormenta(forecast_timeline)

    if indice_granizo["nivel"] in ("Alto", "Moderado"):
        alert_level = "amarilla"
    elif storm_forming:
        alert_level = "amarilla"
    else:
        alert_level = "ninguna"

    return {
        "source": "MET Norway (respaldo: Open-Meteo no disponible en este momento; sin CAPE ni nivel de congelamiento)",
        "source_id": "metno",
        "current_conditions": {
            "temperature_c": details.get("air_temperature"),
            "humidity_pct": details.get("relative_humidity"),
            "wind_speed_kmh": round(wind_speed_kmh, 1) if wind_speed_kmh is not None else None,
            "wind_gusts_kmh": round(wind_gust_kmh, 1) if wind_gust_kmh is not None else None,
            "precipitation_mm": precip_1h if precip_1h is not None else 0,
            "condition_text": condition_text,
            "condition_code": symbol_code,
            "cape": None,
            "freezing_level_m": None,
        },
        "hail_index": indice_granizo,
        "alert_level": alert_level,
        "hail_possible": hail_possible,
        "storm_forming": storm_forming,
        "storm_forming_detail": storm_forming_detail,
        "forecast_timeline": forecast_timeline,
        "degraded": True,
        "degraded_reason": "Open-Meteo no respondió; usando MET Norway como respaldo (sin CAPE ni nivel de congelamiento, el índice de granizo es menos preciso ahora).",
    }


# ============================================================
# TRAYECTORIA Y ETA DE TORMENTA — radar real RMA1 (OHMC/SINARAME)
# ============================================================
# A diferencia del intento anterior con RainViewer (que casi nunca tenía
# señal útil sobre Argentina), esto usa el endpoint interno que usa el
# propio visor web de OHMC (webmet.ohmc.ar) para traer el producto COLMAX
# (máximo de reflectividad en columna) del radar nacional RMA1 — el mismo
# radar que usa el SMN para Córdoba.
#
# OJO: es un endpoint NO documentado del visor de OHMC, no una API pública
# pensada para terceros. Filtra por header Origin/Referer (no por API key),
# así que lo imitamos para poder consultarlo. Puede cambiar o bloquearse
# sin aviso — a diferencia de RainViewer/MET Norway, acá no hay garantía
# de estabilidad. Se decidió asumir ese riesgo porque, a cambio, es el
# radar real: hoy mismo detectó lluvia donde RainViewer no veía nada.
#
# Método: por cada frame (imagen RGBA georreferenciada por un bbox en
# grados), calculamos el centroide ponderado por el canal alfa (igual que
# con RainViewer). Comparando el centroide entre frames sacamos velocidad
# y rumbo, y extrapolamos un ETA — pero esta vez a VARIOS destinos dentro
# de Córdoba Capital (Centro, Norte, Sur, Este, Oeste), no solo a un punto.
# Sigue siendo una ESTIMACIÓN propia (nowcasting lineal simple), no un
# dato oficial, y se degrada rápido más allá de ~60 min.

OHMC_API_BASE = "https://webmet.ohmc.ar/api/v1"
OHMC_HEADERS = {
    "Origin": "https://webmet.ohmc.ar",
    "Referer": "https://webmet.ohmc.ar/",
    "User-Agent": "Mozilla/5.0 (compatible; ClimaCordobaPro/1.0; +https://github.com/seryeil/clima-cordoba-bot)",
}
OHMC_RADAR_CODE = "RMA1"
OHMC_PRODUCT_KEY = "COLMAX"
OHMC_STRATEGY = "0315"
OHMC_VOL_NR = ["01", "02"]
OHMC_FRAMES_A_USAR = 4  # últimos 4 frames (~35-40 min, cada uno cada ~9-10 min)

# Zonas de origen de referencia (para decir "la tormenta está cerca de X").
# Son aproximaciones de centro de localidad/región, no límites oficiales.
ZONAS_REFERENCIA = [
    ("Traslasierra", -31.72, -65.00),
    ("Punilla (Carlos Paz / La Falda)", -31.33, -64.47),
    ("Sierras Chicas", -31.28, -64.33),
    ("Alta Gracia", -31.6539, -64.4282),
    ("Calamuchita", -32.05, -64.50),
    ("Córdoba Capital", LAT, LON),
]

# Destinos dentro de Córdoba Capital para el ETA por zona. Son puntos de
# referencia aproximados de cada cuadrante de la ciudad, no un límite
# barrial oficial — alcanza para dar una idea de "por dónde entra primero".
DESTINOS_CAPITAL = [
    ("Centro", LAT, LON),
    ("Zona Norte (Cerro de las Rosas / Villa Belgrano)", -31.3765, -64.2356),
    ("Zona Sur (Villa El Libertador / Juniors)", -31.4762, -64.1739),
    ("Zona Oeste (Argüello)", -31.3886, -64.2653),
    ("Zona Este (Barrio Jardín / Gral. Paz)", -31.4075, -64.1578),
]


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


RUMBOS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
          "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def _bearing_a_rumbo(brng):
    ix = round(brng / 22.5) % 16
    return RUMBOS[ix]


def _zona_mas_cercana(lat, lon):
    return min(ZONAS_REFERENCIA, key=lambda z: _haversine_km(lat, lon, z[1], z[2]))[0]


def _ohmc_listar_frames():
    """Trae los últimos frames disponibles del producto COLMAX de RMA1,
    ordenados del más viejo al más nuevo."""
    params = {
        "radar_code": OHMC_RADAR_CODE,
        "product_key": OHMC_PRODUCT_KEY,
        "vol_nr": OHMC_VOL_NR,
        "strategy": OHMC_STRATEGY,
    }
    resp = requests.get(f"{OHMC_API_BASE}/cogs", params=params, headers=OHMC_HEADERS, timeout=8)
    resp.raise_for_status()
    cogs = resp.json().get("cogs", [])
    return sorted(cogs, key=lambda c: c["observation_time"])


def _ohmc_descargar_imagen(frame_id):
    url = f"{OHMC_API_BASE}/frames/{frame_id}/image.png"
    resp = requests.get(url, params={"colormap": "OHMC_dBZ"}, headers=OHMC_HEADERS, timeout=10)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA")


def _ohmc_centroide_precipitacion(img):
    """Centroide ponderado por el canal alfa. El fondo (sin eco de radar)
    viene con alpha=0; donde hay reflectividad, alpha=255."""
    pixels = img.load()
    w, h = img.size
    total_peso = 0.0
    sum_x = 0.0
    sum_y = 0.0
    for y in range(h):
        for x in range(w):
            a = pixels[x, y][3]
            if a > 10:
                total_peso += a
                sum_x += x * a
                sum_y += y * a
    if total_peso == 0:
        return None
    return (sum_x / total_peso, sum_y / total_peso, total_peso)


def _ohmc_pixel_a_latlon(px, py, w, h, bbox):
    """La imagen de OHMC es una proyección lineal simple sobre su bbox
    (no es un tile Web Mercator como RainViewer), así que alcanza con
    interpolar linealmente dentro de esos límites."""
    lon = bbox["min_lon"] + (px / w) * (bbox["max_lon"] - bbox["min_lon"])
    lat = bbox["max_lat"] - (py / h) * (bbox["max_lat"] - bbox["min_lat"])
    return lat, lon


def analizar_trayectoria_tormenta():
    try:
        frames = _ohmc_listar_frames()
        if len(frames) < 2:
            return {"ok": False, "error": "No hay suficientes frames de radar todavía (OHMC)."}

        frames_a_usar = frames[-OHMC_FRAMES_A_USAR:] if len(frames) >= OHMC_FRAMES_A_USAR else frames

        puntos = []
        descargas_ok = 0
        for f in frames_a_usar:
            try:
                img = _ohmc_descargar_imagen(f["id"])
                descargas_ok += 1
            except Exception:
                continue
            centroide = _ohmc_centroide_precipitacion(img)
            if centroide is None:
                continue
            px, py, peso = centroide
            w, h = img.size
            lat, lon = _ohmc_pixel_a_latlon(px, py, w, h, f["bbox"])
            t = datetime.strptime(f["observation_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            puntos.append({"time": t.timestamp(), "lat": lat, "lon": lon, "peso": peso})

        diagnostico = {
            "frames_evaluados": len(frames_a_usar),
            "frames_descargados_ok": descargas_ok,
            "frames_con_senal_de_lluvia": len(puntos),
        }

        if len(puntos) < 2:
            if descargas_ok == 0:
                motivo = "No se pudo descargar ningún frame del radar de OHMC (posible problema de red o el endpoint cambió)."
            elif descargas_ok < len(frames_a_usar):
                motivo = "Se descargaron algunos frames pero no todos; puede ser un problema transitorio de red con OHMC."
            else:
                motivo = "Las descargas funcionaron bien; simplemente no hay precipitación detectable en el radar de Córdoba ahora mismo."
            return {
                "ok": False,
                "error": "No se detectó suficiente precipitación en el radar (RMA1/OHMC) para calcular trayectoria.",
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

        etas_por_zona = []
        for nombre, lat_dest, lon_dest in DESTINOS_CAPITAL:
            dist_km = _haversine_km(ultimo["lat"], ultimo["lon"], lat_dest, lon_dest)
            if velocidad_kmh < 2:
                eta_min = None
            else:
                eta_min = round((dist_km / velocidad_kmh) * 60)
            etas_por_zona.append({
                "zona": nombre,
                "distancia_km": round(dist_km, 1),
                "eta_minutos": eta_min,
            })

        nota_estacionaria = None
        if velocidad_kmh < 2:
            nota_estacionaria = "El sistema se mueve muy poco o está estacionario; no se puede estimar una hora de llegada confiable."

        return {
            "ok": True,
            "zona_actual_aproximada": _zona_mas_cercana(ultimo["lat"], ultimo["lon"]),
            "rumbo_grados": round(rumbo),
            "rumbo_compass": _bearing_a_rumbo(rumbo),
            "velocidad_estim_kmh": round(velocidad_kmh, 1),
            "etas_por_zona": etas_por_zona,
            "nota_estacionaria": nota_estacionaria,
            "frames_usados": len(puntos),
            "fuente": "Radar RMA1 (SINARAME), vía OHMC — producto COLMAX (máximo de reflectividad en columna)",
            "metodo": "Centroide ponderado de reflectividad (dBZ) sobre imagen georreferenciada, comparado entre frames (nowcasting lineal). Estimación propia, no oficial.",
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


# ============================================================
# OPEN-METEO: pedido, circuit breaker y estadísticas
# ============================================================

class OpenMeteoRateLimited(Exception):
    """Open-Meteo devolvió 429. Lleva el Retry-After (si lo mandó) para dimensionar la pausa."""

    def __init__(self, retry_after=None):
        super().__init__("429 Too Many Requests (Open-Meteo)")
        self.retry_after = retry_after


def _parse_retry_after(valor):
    try:
        v = int(float(valor))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _fetch_open_meteo():
    resp = requests.get(OPEN_METEO_URL, timeout=8)
    if resp.status_code == 429:
        raise OpenMeteoRateLimited(_parse_retry_after(resp.headers.get("Retry-After")))
    resp.raise_for_status()
    return resp.json()


def _fetch_open_meteo_con_reintento():
    """Un pedido a Open-Meteo. Un 429 NO se reintenta (insistir al instante no sirve
    y empeora el límite; de eso se encarga el circuit breaker). Otros errores
    (timeout, 5xx) sí tienen un reintento corto."""
    try:
        return _fetch_open_meteo()
    except OpenMeteoRateLimited:
        raise
    except Exception:
        time.sleep(1.5)
        return _fetch_open_meteo()


def _breaker_abierto(now_ts):
    return now_ts < _breaker["open_until"]


def _abrir_breaker(now_ts, segundos, motivo):
    segundos = max(BREAKER_MIN, min(BREAKER_MAX, segundos))
    _breaker["open_until"] = now_ts + segundos
    _breaker["motivo"] = motivo


def _cerrar_breaker():
    _breaker["open_until"] = 0.0
    _breaker["motivo"] = None


def _registrar_ok(fuente, now_ts):
    s = _source_stats[fuente]
    s["ok_count"] += 1
    s["last_ok_ts"] = now_ts


def _registrar_error(fuente, now_ts, e):
    s = _source_stats[fuente]
    s["error_count"] += 1
    s["last_error_ts"] = now_ts
    s["last_error"] = str(e)[:200]


def _armar_datos_open_meteo(meteo, now, current_time_str):
    """Convierte la respuesta cruda de Open-Meteo en el payload que consume el frontend."""
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

    # Timeline real: próximas horas con probabilidad de precipitación (dato real)
    forecast_timeline = []
    for h in range(start_idx, min(start_idx + 4, len(hourly_times))):
        hour_label = hourly_times[h].split("T")[1]
        forecast_timeline.append({
            "hour": hour_label,
            "precip_probability": hourly_probs[h] if h < len(hourly_probs) else None
        })

    storm_forming, storm_forming_detail = evaluar_formacion_tormenta(forecast_timeline)

    # Heurística de severidad general (combina el índice de granizo, lluvia/viento
    # ACTUALES, y el pronóstico de las próximas horas para no depender solo del
    # instante presente: una tormenta puede estar "en formación" antes de que el
    # dato instantáneo lo muestre).
    if indice_granizo["nivel"] == "Alto":
        alert_level = "naranja"
    elif indice_granizo["nivel"] == "Moderado" or wind_gusts >= 60 or precipitation >= 10:
        alert_level = "amarilla"
    elif storm_forming:
        alert_level = "amarilla"
    else:
        alert_level = "ninguna"

    return {
        "source": "Open-Meteo (datos reales, modelo meteorológico, no es un parte oficial del SMN)",
        "source_id": "open-meteo",
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
        "storm_forming": storm_forming,
        "storm_forming_detail": storm_forming_detail,
        "forecast_timeline": forecast_timeline,
        "degraded": False,
        "smn_official_url": "https://www.smn.gob.ar/pronostico/cordoba",
        "last_update": current_time_str,
        "ok": True,
        "stale": False,
    }


def _clima_desde_cache(now_ts):
    cache_ttl = CACHE_TTL_WEATHER_OK if _weather_cache.get("ok") else CACHE_TTL_WEATHER_ERROR
    if _weather_cache["data"] is not None and (now_ts - _weather_cache["ts"]) < cache_ttl:
        return _weather_cache["data"]
    return None


def _refrescar_clima(now_ts):
    """Obtiene datos frescos: Open-Meteo (si el breaker lo permite) -> MET Norway
    -> último dato bueno marcado como desactualizado -> error explícito."""
    now = datetime.now(arg_tz)
    current_time_str = now.strftime("%d/%m/%Y %H:%M:%S")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    data = None
    error_primary = None

    if _breaker_abierto(now_ts):
        resto = int(_breaker["open_until"] - now_ts)
        error_primary = f"Open-Meteo en pausa ~{resto}s más ({_breaker['motivo']})"
    else:
        try:
            meteo = _fetch_open_meteo_con_reintento()
            data = _armar_datos_open_meteo(meteo, now, current_time_str)
            _registrar_ok("open-meteo", now_ts)
            _cerrar_breaker()
        except Exception as e:
            error_primary = e
            _registrar_error("open-meteo", now_ts, e)
            if isinstance(e, OpenMeteoRateLimited):
                _abrir_breaker(now_ts, e.retry_after or BREAKER_COOLDOWN_429, "429 Too Many Requests")
            else:
                _abrir_breaker(now_ts, BREAKER_COOLDOWN_ERROR, str(e)[:120])

    if data is None:
        # Open-Meteo no respondió (ej. 429 por IP compartida de Render, no
        # necesariamente por nuestro propio tráfico) o está en pausa. Antes de
        # resignarnos a mostrar datos viejos, probamos la fuente de respaldo.
        try:
            fallback = _fetch_metno()
            data = {
                **fallback,
                "smn_official_url": "https://www.smn.gob.ar/pronostico/cordoba",
                "last_update": current_time_str,
                "ok": True,
                "stale": False,
            }
            _registrar_ok("metno", now_ts)
        except Exception as e_fallback:
            _registrar_error("metno", now_ts, e_fallback)
            # Tampoco funcionó el respaldo: mostramos el último dato real
            # que sí tuvimos (de cualquiera de las dos fuentes), marcado
            # como desactualizado. Solo si nunca tuvimos ningún dato bueno
            # mostramos el error explícito.
            if _weather_last_good["data"] is not None:
                data = dict(_weather_last_good["data"])
                edad_min = round((now_ts - _weather_last_good["ts"]) / 60, 1)
                data["stale"] = True
                data["stale_minutes"] = edad_min
                data["stale_reason"] = "No se pudo actualizar ahora (fallaron Open-Meteo y el respaldo MET Norway); mostrando el último dato real obtenido."
            else:
                data = {
                    "ok": False,
                    "error": "No se pudo obtener el pronóstico en este momento (fallaron ambas fuentes).",
                    "detail": f"Open-Meteo: {error_primary} | MET Norway: {e_fallback}",
                    "smn_official_url": "https://www.smn.gob.ar/pronostico/cordoba",
                    "last_update": current_time_str,
                }
            _weather_cache["ok"] = False
            _weather_cache["data"] = data
            _weather_cache["ts"] = now_ts
            return data

    # Éxito (de cualquiera de las dos fuentes): lo guardamos como el último dato
    # bueno, para poder usarlo si el próximo pedido falla.
    data["fetched_at"] = fetched_at
    _weather_last_good["data"] = data
    _weather_last_good["ts"] = now_ts
    _weather_cache["ok"] = True
    _weather_cache["data"] = data
    _weather_cache["ts"] = now_ts
    return data


def obtener_clima():
    """Punto único de acceso al clima (lo usan /api/live-weather, /api/health?warm=1
    y el precalentamiento al arrancar). Caché + lock anti-avalancha."""
    now_ts = time.time()
    cached = _clima_desde_cache(now_ts)
    if cached is not None:
        return cached
    with _refresh_lock:
        # Otro pedido pudo haber refrescado mientras esperábamos el lock.
        now_ts = time.time()
        cached = _clima_desde_cache(now_ts)
        if cached is not None:
            return cached
        return _refrescar_clima(now_ts)


@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    return jsonify(obtener_clima())


# ============================================================
# /api/health: estado del servicio (para monitoreo y para mantener despierto Render)
# ============================================================

def _iso_utc(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _stats_publicas(fuente):
    s = _source_stats[fuente]
    return {
        "ok_count": s["ok_count"],
        "error_count": s["error_count"],
        "last_ok_utc": _iso_utc(s["last_ok_ts"]),
        "last_error_utc": _iso_utc(s["last_error_ts"]),
        "last_error": s["last_error"],
    }


def _estado_servicio(now_ts):
    """ok | degradado | desactualizado | sin_datos"""
    lg = _weather_last_good["data"]
    if lg is None:
        return "sin_datos", None
    edad = now_ts - _weather_last_good["ts"]
    if edad >= HEALTH_MAX_AGE_OK:
        return "desactualizado", edad
    actual = _weather_cache["data"] or {}
    if lg.get("source_id") != "open-meteo" or actual.get("stale") is True or _breaker_abierto(now_ts):
        return "degradado", edad
    return "ok", edad


@app.route('/api/health', methods=['GET'])
def health():
    """Responde siempre 200 mientras el proceso esté vivo (el estado va en el cuerpo).
    Sin parámetros NO consulta fuentes externas. Con ?warm=1 además refresca el clima
    si la caché venció: sirve para el ping periódico que mantiene despierto el
    servidor y deja datos listos en memoria."""
    if request.args.get("warm") in ("1", "true", "yes"):
        try:
            obtener_clima()
        except Exception:
            pass

    now_ts = time.time()
    estado, edad = _estado_servicio(now_ts)
    lg = _weather_last_good["data"]
    cache_actual = _weather_cache["data"] or {}

    return jsonify({
        "status": estado,
        "uptime_s": round(now_ts - _started_ts),
        "server_time_utc": _iso_utc(now_ts),
        "weather": {
            "source_id": lg.get("source_id") if lg else None,
            "age_s": round(edad) if edad is not None else None,
            "serving_stale": bool(cache_actual.get("stale")),
            "serving_degraded": bool(cache_actual.get("degraded")),
        },
        "open_meteo": {
            "breaker_open": _breaker_abierto(now_ts),
            "breaker_retry_in_s": max(0, round(_breaker["open_until"] - now_ts)) if _breaker_abierto(now_ts) else 0,
            "breaker_reason": _breaker["motivo"] if _breaker_abierto(now_ts) else None,
            **_stats_publicas("open-meteo"),
        },
        "metno": _stats_publicas("metno"),
    })


def _precalentar():
    """Al arrancar (o despertar) el servidor, trae el clima en segundo plano para que
    el primer usuario no encuentre la memoria vacía."""
    try:
        obtener_clima()
    except Exception:
        pass


# En los tests se desactiva con CLIMA_NO_WARMUP=1 para no pegarle a la red al importar.
if os.environ.get("CLIMA_NO_WARMUP") != "1":
    threading.Thread(target=_precalentar, daemon=True).start()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
