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
            "status": "Avanzando hacia el este",
            "origin": "Sierras Chicas (Calera / Argüello Norte)",
            "destination": "Córdoba Capital",
            "route_steps": ["Sierras Chicas", "Zona Noroeste (Argüello/Saldán)", "Córdoba Capital (Centro/Nueva Cba)"],
            "eta_minutes": 35,  # Minutos exactos para cálculos dinámicos
            "eta_display": "35 min",
            "hail_probability": "Moderada (Zonas Altas)",
            "wind_speed": "38 km/h (Ráfagas)",
            "accumulated_rain": "14 mm (Estimado)"
        },
        "dimarco_tweets": [
            {
                "id": 1,
                "time": "Hace 10 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Actualización de radar: Núcleos con actividad eléctrica ingresando al oeste provincial con desplazamiento hacia el Gran Córdoba.",
                "interaction_summary": "💬 18 respuestas · 🔄 24 RT · ❤️ 95 Me gusta",
                "user_replies": [
                    {"user": "@marcos_cba", "text": "Acá por Villa Allende se nubló de golpe y sopla viento fuerte."},
                    {"user": "@valeria_met", "text": "¡Impresionante cómo oscureció hacia el oeste en Mendiolaza!"}
                ]
            },
            {
                "id": 2,
                "time": "Hace 40 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Respuesta a consulta de seguidores: Se mantiene la probabilidad de ráfagas sectorizadas hacia la tarde. Precaución en rutas.",
                "interaction_summary": "💬 9 respuestas · 🔄 12 RT · ❤️ 51 Me gusta",
                "user_replies": [
                    {"user": "@clima_unvm", "text": "Gracias Rafa por el aviso, atento a las rutas provinciales."}
                ]
            }
        ],
        "last_update": current_time_str
    }
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
