from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "🤖 ¡El bot meteorológico para Córdoba Capital y Gran Córdoba está online y funcionando!"

@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    current_time_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    data = {
        "smn_status": "Monitoreo activo",
        "alerts": [
            {
                "title": "Sistema de Alerta Temprana - Córdoba Capital",
                "severity": "Normal (Verde)",
                "description": "Monitoreo permanente de núcleos convectivos aislados por altas temperaturas y humedad en las sierras."
            }
        ],
        "storm_trajectory": {
            "status": "Monitoreando celdas en desplazamiento",
            "origin": "Sierras Chicas / Gran Córdoba",
            "destination": "Córdoba Capital",
            "eta_minutes": "30-40 min",
            "hail_probability": "Moderada (Zonas Altas)",
            "wind_speed": "38 km/h (Ráfagas)",
            "accumulated_rain": "14 mm (Estimado)"
        },
        "dimarco_tweets": [
            {
                "time": "Hace 10 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Actualización de radar: Núcleos con actividad eléctrica ingresando al oeste provincial con desplazamiento hacia el Gran Córdoba.",
                "interaction": "💬 18 respuestas · 🔄 24 RT · ❤️ 95 Me gusta"
            },
            {
                "time": "Hace 40 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Respuesta a consulta de seguidores: Se mantiene la probabilidad de ráfagas sectorizadas hacia la tarde. Precaución en rutas.",
                "interaction": "💬 9 respuestas · 🔄 12 RT · ❤️ 51 Me gusta"
            },
            {
                "time": "Hace 1 hora",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Inestabilidad marcada en toda la región centro y norte. Seguimiento satelital en vivo de celdas aisladas.",
                "interaction": "💬 15 respuestas · 🔄 30 RT · ❤️ 120 Me gusta"
            }
        ],
        "last_update": current_time_str
    }
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
