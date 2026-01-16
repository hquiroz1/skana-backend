"""
SKANA Backend - Notificaciones Push para Apuestas
Diseñado para ejecutar en Railway.app
"""

import os
import json
import time
import requests
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore, messaging

# =====================
# CONFIGURACIÓN
# =====================
FOOTBALL_API_KEY = os.environ.get('FOOTBALL_API_KEY', '7ddda5241ad74811929323c8e39aa0db')
FOOTBALL_API_URL = 'https://api.football-data.org/v4'
CHECK_INTERVAL = 60  # segundos

# =====================
# INICIALIZAR FIREBASE
# =====================
def init_firebase():
    """Inicializa Firebase usando credenciales desde variable de entorno o archivo"""
    print("🔥 Inicializando Firebase...")
    
    try:
        # Opción 1: Credenciales desde variable de entorno (Railway)
        if os.environ.get('FIREBASE_CREDENTIALS'):
            cred_dict = json.loads(os.environ['FIREBASE_CREDENTIALS'])
            cred = credentials.Certificate(cred_dict)
        # Opción 2: Archivo local (desarrollo)
        elif os.path.exists('firebase-credentials.json'):
            cred = credentials.Certificate('firebase-credentials.json')
        else:
            raise Exception("No se encontraron credenciales de Firebase")
        
        firebase_admin.initialize_app(cred)
        print(f"✅ Firebase conectado - Proyecto: {firebase_admin.get_app().project_id}")
        return True
    except Exception as e:
        print(f"❌ Error inicializando Firebase: {e}")
        return False

# =====================
# OBTENER PARTIDOS
# =====================
def get_live_matches():
    """Obtiene partidos en vivo y finalizados hoy"""
    try:
        headers = {'X-Auth-Token': FOOTBALL_API_KEY}
        
        # Intentar partidos en vivo
        response = requests.get(
            f'{FOOTBALL_API_URL}/matches?status=LIVE,IN_PLAY,PAUSED,FINISHED',
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get('matches', [])
        else:
            # Fallback: partidos de hoy
            today = datetime.now().strftime('%Y-%m-%d')
            response = requests.get(
                f'{FOOTBALL_API_URL}/matches?dateFrom={today}&dateTo={today}',
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                return response.json().get('matches', [])
        
        return []
    except Exception as e:
        print(f"   ⚠️ Error obteniendo partidos: {e}")
        return []

# =====================
# OBTENER DISPOSITIVOS
# =====================
def get_devices():
    """Obtiene todos los dispositivos registrados de Firestore"""
    try:
        db = firestore.client()
        devices_ref = db.collection('devices')
        devices = []
        
        for doc in devices_ref.stream():
            device_data = doc.to_dict()
            if device_data.get('token'):
                devices.append({
                    'id': doc.id,
                    'token': device_data['token']
                })
        
        return devices
    except Exception as e:
        print(f"   ⚠️ Error obteniendo dispositivos: {e}")
        return []

# =====================
# OBTENER TICKETS
# =====================
def get_all_tickets():
    """Obtiene todos los tickets de todos los usuarios"""
    try:
        db = firestore.client()
        users_ref = db.collection('users')
        all_tickets = []
        
        for doc in users_ref.stream():
            user_data = doc.to_dict()
            tickets = user_data.get('tickets', [])
            for ticket in tickets:
                ticket['userId'] = doc.id
                all_tickets.append(ticket)
        
        return all_tickets
    except Exception as e:
        print(f"   ⚠️ Error obteniendo tickets: {e}")
        return []

# =====================
# ENVIAR NOTIFICACIÓN
# =====================
def send_notification(token, title, body, data=None):
    """Envía una notificación push a un dispositivo"""
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            data=data or {},
            token=token
        )
        
        response = messaging.send(message)
        print(f"   📤 Notificación enviada: {title}")
        return True
    except Exception as e:
        print(f"   ❌ Error enviando notificación: {e}")
        return False

# =====================
# PROCESAR PARTIDOS
# =====================
# Cache para evitar notificaciones duplicadas
notified_events = set()
# Cache para guardar marcadores anteriores
previous_scores = {}

def process_matches(matches, tickets, devices):
    """Procesa partidos y envía notificaciones según los tickets"""
    global previous_scores
    
    for ticket in tickets:
        if ticket.get('status') == 'won' or ticket.get('status') == 'lost':
            continue  # Ticket ya finalizado
        
        for bet in ticket.get('bets', []):
            match_id = bet.get('matchId')
            if not match_id:
                continue
            
            # Buscar el partido en vivo
            match = next((m for m in matches if m.get('id') == match_id), None)
            if not match:
                continue
            
            home_team = match.get('homeTeam', {}).get('name', 'Local')
            away_team = match.get('awayTeam', {}).get('name', 'Visitante')
            match_status = match.get('status')
            
            # Obtener marcador actual
            score = match.get('score', {})
            home_score = score.get('fullTime', {}).get('home') or score.get('halfTime', {}).get('home') or 0
            away_score = score.get('fullTime', {}).get('away') or score.get('halfTime', {}).get('away') or 0
            current_score = f"{home_score}-{away_score}"
            
            # Obtener marcador anterior
            prev = previous_scores.get(match_id, {'home': 0, 'away': 0})
            prev_home = prev.get('home', 0)
            prev_away = prev.get('away', 0)
            
            # Evento: Partido comenzó
            event_key = f"{match_id}_started"
            if match_status in ['IN_PLAY', 'LIVE'] and event_key not in notified_events:
                notified_events.add(event_key)
                for device in devices:
                    send_notification(
                        device['token'],
                        f"🏟️ ¡Comenzó! {home_team} vs {away_team}",
                        "Tu apuesta está en juego",
                        {'matchId': str(match_id), 'type': 'started'}
                    )
            
            # Evento: Gol (detectar quién marcó)
            score_key = f"{match_id}_score_{home_score}_{away_score}"
            if match_status in ['IN_PLAY', 'LIVE', 'PAUSED'] and score_key not in notified_events:
                # Detectar si hubo gol
                if home_score > prev_home:
                    # Gol del equipo local
                    notified_events.add(score_key)
                    for device in devices:
                        send_notification(
                            device['token'],
                            f"⚽ ¡GOL de {home_team}!",
                            f"{home_team} {home_score} - {away_score} {away_team}",
                            {'matchId': str(match_id), 'type': 'goal', 'scorer': 'home'}
                        )
                elif away_score > prev_away:
                    # Gol del equipo visitante
                    notified_events.add(score_key)
                    for device in devices:
                        send_notification(
                            device['token'],
                            f"⚽ ¡GOL de {away_team}!",
                            f"{home_team} {home_score} - {away_score} {away_team}",
                            {'matchId': str(match_id), 'type': 'goal', 'scorer': 'away'}
                        )
            
            # Actualizar marcador anterior
            previous_scores[match_id] = {'home': home_score, 'away': away_score}
            
            # Evento: Partido terminó
            event_key = f"{match_id}_finished"
            if match_status == 'FINISHED' and event_key not in notified_events:
                notified_events.add(event_key)
                
                # Evaluar apuesta según tipo
                selection = bet.get('selection', '1')
                total_goals = home_score + away_score
                
                won = evaluate_bet(selection, home_score, away_score, total_goals)
                
                for device in devices:
                    if won:
                        send_notification(
                            device['token'],
                            f"🎉 ¡GANASTE! {home_team} vs {away_team}",
                            f"Final: {home_score} - {away_score}",
                            {'matchId': str(match_id), 'type': 'won'}
                        )
                    else:
                        send_notification(
                            device['token'],
                            f"😢 Perdiste: {home_team} vs {away_team}",
                            f"Final: {home_score} - {away_score}",
                            {'matchId': str(match_id), 'type': 'lost'}
                        )

def evaluate_bet(selection, home_score, away_score, total_goals):
    """Evalúa si la apuesta fue ganadora según el tipo de mercado"""
    
    # 1X2 - Resultado final
    if selection == '1':
        return home_score > away_score
    elif selection == 'X':
        return home_score == away_score
    elif selection == '2':
        return away_score > home_score
    
    # Doble Oportunidad
    elif selection == '1X':
        return home_score >= away_score
    elif selection == 'X2':
        return away_score >= home_score
    elif selection == '12':
        return home_score != away_score
    
    # Over/Under
    elif selection == 'O1.5':
        return total_goals > 1.5
    elif selection == 'O2.5':
        return total_goals > 2.5
    elif selection == 'O3.5':
        return total_goals > 3.5
    elif selection == 'U1.5':
        return total_goals < 1.5
    elif selection == 'U2.5':
        return total_goals < 2.5
    elif selection == 'U3.5':
        return total_goals < 3.5
    
    # BTTS (Ambos marcan)
    elif selection == 'BTTS_Y':
        return home_score > 0 and away_score > 0
    elif selection == 'BTTS_N':
        return home_score == 0 or away_score == 0
    
    # Handicap
    elif selection == 'H1-1':
        return (home_score - 1) > away_score
    elif selection == 'H1+1':
        return (home_score + 1) > away_score
    elif selection == 'H2-1':
        return away_score - 1 > home_score
    elif selection == 'H2+1':
        return away_score + 1 > home_score
    
    # Resultado exacto
    elif selection.startswith('CS'):
        exact_score = selection.replace('CS', '')
        parts = exact_score.split('-')
        if len(parts) == 2:
            expected_home = int(parts[0])
            expected_away = int(parts[1])
            return home_score == expected_home and away_score == expected_away
    
    # Default: no ganó
    return False

# =====================
# LOOP PRINCIPAL
# =====================
def main():
    print("=" * 50)
    print("🎯 SKANA - Backend de Notificaciones")
    print("=" * 50)
    print(f"⏱️  Intervalo de revisión: {CHECK_INTERVAL} segundos")
    print("   Presiona Ctrl+C para detener")
    print()
    
    if not init_firebase():
        print("❌ No se pudo inicializar Firebase. Saliendo...")
        return
    
    while True:
        try:
            now = datetime.now().strftime('%H:%M:%S')
            print(f"⏰ {now} - Revisando partidos...")
            
            # Obtener datos
            matches = get_live_matches()
            devices = get_devices()
            tickets = get_all_tickets()
            
            print(f"   📊 {len(matches)} partidos encontrados")
            print(f"   📱 {len(devices)} dispositivos registrados")
            print(f"   🎫 {len(tickets)} tickets activos")
            
            # Procesar
            if matches and devices and tickets:
                process_matches(matches, tickets, devices)
            
        except Exception as e:
            print(f"   ❌ Error en loop principal: {e}")
        
        # Esperar antes de la siguiente revisión
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
