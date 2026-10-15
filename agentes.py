import math
import requests

class AgenteMeteorologo:
    """Agente 1: Recopila los datos del clima en tiempo real desde Open-Meteo."""
    def __init__(self, lat, lon):
        self.lat = lat
        self.lon = lon

    def obtener_estado_actual(self):
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.lat}&longitude={self.lon}"
            "&current=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_gusts_10m,weather_code"
            "&hourly=precipitation_probability,cape,freezing_level_height"
            "&timezone=America%2FArgentina%2FCordoba&forecast_days=1"
        )
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        return resp.json()


class AgenteTermodinamicoGranizo:
    """Agente 2 (Sensible/Preventivo): Analiza la severidad y el potencial de granizo 
    con umbrales más bajos para garantizar una alerta temprana."""
    def evaluar_riesgo(self, cape, freezing_level_m, weather_code, wind_gusts_kmh):
        score = 0
        detail = []

        # Umbrales de CAPE más sensibles para detectar inestabilidad antes
        if cape is not None:
            if cape >= 3000:
                score += 45
                detail.append(f"CAPE muy alto ({cape:.0f} J/kg)")
            elif cape >= 1800:
                score += 35
                detail.append(f"CAPE alto ({cape:.0f} J/kg)")
            elif cape >= 800:
                score += 20
                detail.append(f"CAPE moderado ({cape:.0f} J/kg)")
            elif cape >= 300:
                score += 10
                detail.append(f"CAPE bajo / inestabilidad inicial ({cape:.0f} J/kg)")

        # Nivel de congelamiento (si está bajo, favorece que la piedra no se derrita)
        if freezing_level_m is not None:
            if freezing_level_m <= 3200:
                score += 25
                detail.append(f"nivel de congelamiento bajo ({freezing_level_m:.0f} m)")
            elif freezing_level_m <= 4200:
                score += 15
                detail.append(f"nivel de congelamiento moderado ({freezing_level_m:.0f} m)")

        HAIL_CODES = {96, 99}
        if weather_code in HAIL_CODES:
            score += 30
            detail.append("modelo indica tormenta con granizo")
        elif weather_code == 95:
            score += 15
            detail.append("modelo indica tormenta eléctrica")

        # Ráfagas de viento más sensibles
        if wind_gusts_kmh is not None:
            if wind_gusts_kmh >= 65:
                score += 15
                detail.append(f"ráfagas fuertes ({wind_gusts_kmh:.0f} km/h)")
            elif wind_gusts_kmh >= 45:
                score += 8
                detail.append(f"ráfagas moderadas ({wind_gusts_kmh:.0f} km/h)")

        score = min(score, 100)
        
        # Umbrales de nivel más accesibles para activar avisos preventivos
        if score >= 50:
            nivel = "Alto"
        elif score >= 28:
            nivel = "Moderado"
        elif score >= 12:
            nivel = "Bajo"
        else:
            nivel = "Muy bajo"

        return {"score": score, "nivel": nivel, "factores": detail}


class AgenteGeoespacialETA:
    """Agente 3: Calcula distancias geográficas y Tiempos Estimados de Llegada (ETA) por zonas y barrios."""
    def __init__(self, destinos):
        self.destinos = destinos

    def _haversine_km(self, lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    def calcular_etas(self, lat_actual, lon_actual, velocidad_kmh):
        etas = []
        for nombre, lat_dest, lon_dest in self.destinos:
            dist_km = self._haversine_km(lat_actual, lon_actual, lat_dest, lon_dest)
            if velocidad_kmh < 2:
                eta_min = None
            else:
                eta_min = round((dist_km / velocidad_kmh) * 60)
            etas.append({
                "zona": nombre,
                "distancia_km": round(dist_km, 1),
                "eta_minutos": eta_min
            })
        return etas
