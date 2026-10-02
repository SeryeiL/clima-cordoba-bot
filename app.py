from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "🤖 ¡El bot meteorológico para Córdoba Capital y Gran Córdoba está online y funcionando!"

@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    data = {
        "smn_status": "Monitoreo activo",
        "alerts": [
            {
                "title": "Sistema de Alerta Temprana - Córdoba Capital",
                "description": "Sin alertas severas rojas activas. Vigilar desarrollo de núcleos aislados por humedad y calor extremo en las sierras."
            },
            {
                "title": "Aviso a Corto Plazo (ACP) - Gran Córdoba",
                "description": "Posibilidad de chaparrones aislados con ráfagas sectorizadas en zona norte y oeste."
            }
        ],
        "storm_trajectory": {
            "status": "Monitoreando celdas en desplazamiento",
            "origin": "Sierras Chicas / Alta Gracia",
            "destination": "Córdoba Capital",
            "eta_minutes": "35-45 min",
            "hail_probability": "Moderada (Zonas Altas)",
            "wind_speed": "35 km/h (Ráfagas de 55 km/h)",
            "accumulated_rain": "12 mm (Estimado)"
        },
        "dimarco_tweets": [
            {
                "time": "Hace 15 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Atentos por la zona oeste provincial, núcleo ingresando con actividad eléctrica importante y ocasional caída de granizo pequeño en altura.",
                "interaction": "💬 14 respuestas · 🔄 32 RT · ❤️ 112 Me gusta"
            },
            {
                "time": "Hace 45 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Seguimiento satelital y radar de las condiciones de inestabilidad sobre Sierras Chicas. Se desplaza lento hacia Capital.",
                "interaction": "💬 8 respuestas · 🔄 19 RT · ❤️ 64 Me gusta"
            },
            {
                "time": "Hace 2 horas",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Respuesta a seguidor: Sí, se esperan marcas térmicas elevadas antes del ingreso del frente húmedo hacia la tarde en todo el Gran Córdoba.",
                "interaction": "💬 5 respuestas · 🔄 4 RT · ❤️ 28 Me gusta"
            }
        ],
        "last_update": "02/10/2026 15:45:00"
    }
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
