# /home/DanDev/terrarium_webapp/app.py
# --- Imports ---
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, abort
import mysql.connector
from mysql.connector import Error
import os
from datetime import datetime, date, timedelta, time
import math
from collections import defaultdict
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import logging
from functools import wraps
import json

app = Flask(__name__)

# --- Logging Configuration ---
logging.basicConfig(level=logging.DEBUG)

# --- Secret Key Configuration ---
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(16))
app.logger.info(f"Flask secret key {'loaded from env' if os.environ.get('FLASK_SECRET_KEY') else 'generated dynamically'}.")

# --- Database Configuration ---
DB_HOST = 'localhost'; DB_USER = 'terrarium_user'; DB_PASSWORD = 'Life4588'; DB_NAME = 'terrarium_data'
app.logger.info(f"Database configured for {DB_USER}@{DB_HOST}/{DB_NAME}")

# --- Database Connection ---
def get_db_connection():
    conn = None
    try:
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, connect_timeout=5)
        if conn.is_connected():
            app.logger.debug("Database connection successful.")
            return conn
    except Error as e:
        app.logger.error(f"Error connecting to DB for web app: {e}")
        if conn and conn.is_connected(): conn.close()
    return None

# --- Helper for Authentication ---
def login_required(f):
    """Decorator to ensure user is logged in before accessing a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session.get('user_id'):
            app.logger.warning(f"Unauthorized access attempt to {request.path} - login required or session invalid.")
            return jsonify({'success': False, 'message': 'Authentication required.'}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- Get interval key ---
def get_interval_key(reading_time, interval_minutes):
    if not isinstance(reading_time, datetime): app.logger.error(f"Invalid type for reading_time: {type(reading_time)}"); return "InvalidTime"
    if interval_minutes <= 0: app.logger.warning(f"Invalid interval <= 0: {interval_minutes}. Using 1."); interval_minutes = 1
    if interval_minutes >= 1440: interval_start_dt = datetime.combine(reading_time.date(), time.min); return interval_start_dt.strftime('%Y-%m-%d %H:%M')
    elif interval_minutes >= 60: hours = interval_minutes // 60; interval_start_hour = (reading_time.hour // hours) * hours; interval_start_dt = reading_time.replace(hour=interval_start_hour, minute=0, second=0, microsecond=0); return interval_start_dt.strftime('%Y-%m-%d %H:%M')
    elif interval_minutes >= 1: minutes_past_hour = reading_time.minute; interval_start_minute = (minutes_past_hour // interval_minutes) * interval_minutes; interval_start_dt = reading_time.replace(minute=interval_start_minute, second=0, microsecond=0); return interval_start_dt.strftime('%Y-%m-%d %H:%M')
    else: app.logger.error(f"Unexpected interval value: {interval_minutes}"); return reading_time.strftime('%Y-%m-%d %H:%M')

# --- Fetch and process data ---
def fetch_and_process_data(conn, device_unique_id, start_dt_query, end_dt_exclusive, interval_minutes):
    cursor = None; final_labels = []; final_temps = []; final_humids = []; gaps_identified = []
    app.logger.debug(f"fetch_and_process_data: device={device_unique_id}, start={start_dt_query}, end={end_dt_exclusive}, interval={interval_minutes}")
    try:
        if not all([device_unique_id, isinstance(start_dt_query, datetime), isinstance(end_dt_exclusive, datetime)]): raise ValueError("Missing params or invalid types.")
        if interval_minutes <= 0: interval_minutes = 1
        where_clause = "WHERE device_unique_id = %s AND reading_time >= %s AND reading_time < %s"
        query_params = (device_unique_id, start_dt_query, end_dt_exclusive)
        query = f"SELECT reading_time, temperature, humidity FROM readings {where_clause} ORDER BY reading_time ASC"
        cursor = conn.cursor(dictionary=True); cursor.execute(query, query_params); rows = cursor.fetchall()
        app.logger.info(f"Fetched {len(rows)} points for device {device_unique_id} [{start_dt_query} - {end_dt_exclusive}].")
        aggregated_data = defaultdict(lambda: {'sum_temp': 0.0, 'sum_humid': 0.0, 'count': 0})
        for row in rows:
            temp_db = row.get('temperature'); humid_db = row.get('humidity'); reading_time = row.get('reading_time')
            if temp_db is None or humid_db is None or not isinstance(reading_time, datetime): continue
            try: temp = float(temp_db); humid = float(humid_db)
            except (ValueError, TypeError) as e: app.logger.warning(f"Data conversion error: {e}. Skipping row."); continue
            interval_key = get_interval_key(reading_time, interval_minutes)
            aggregated_data[interval_key]['sum_temp'] += temp; aggregated_data[interval_key]['sum_humid'] += humid; aggregated_data[interval_key]['count'] += 1
        averaged_data_map = {key: {'temp': round(data['sum_temp'] / data['count'], 2), 'humid': round(data['sum_humid'] / data['count'], 2)} for key, data in aggregated_data.items() if data['count'] > 0}
        current_dt_label_key_start = get_interval_key(start_dt_query, interval_minutes)
        current_dt = datetime.strptime(current_dt_label_key_start, '%Y-%m-%d %H:%M')
        interval = timedelta(minutes=interval_minutes); in_gap = False; gap_start_label = None
        while current_dt < end_dt_exclusive:
            current_label_key = get_interval_key(current_dt, interval_minutes); final_labels.append(current_label_key)
            if current_label_key in averaged_data_map:
                data_point = averaged_data_map[current_label_key]; final_temps.append(data_point['temp']); final_humids.append(data_point['humid'])
                if in_gap: last_null_label = get_interval_key(current_dt - interval, interval_minutes); gaps_identified.append({"start": gap_start_label, "end": last_null_label}); in_gap = False; gap_start_label = None
            else:
                final_temps.append(None); final_humids.append(None)
                if not in_gap: in_gap = True; gap_start_label = current_label_key
            current_dt += interval
        if in_gap: last_null_label = get_interval_key(current_dt - interval, interval_minutes); gaps_identified.append({"start": gap_start_label, "end": last_null_label})
    except Error as e: app.logger.error(f"DB error fetch/process device {device_unique_id}: {e}"); raise
    except ValueError as e: app.logger.error(f"Value error fetch/process device {device_unique_id}: {e}"); raise
    except Exception as e: app.logger.error(f"Unexpected error fetch/process device {device_unique_id}: {e}", exc_info=True); raise
    finally:
        if cursor: cursor.close()
    return final_labels, final_temps, final_humids, gaps_identified

# --- Routes ---
@app.route('/')
def index():
    app.logger.debug("Accessing index route.")
    return render_template('index.html', title='Terrarium Monitor')

@app.route('/login')
def login_page():
    app.logger.debug("Accessing login route.")
    if 'logged_in' in session: return redirect(url_for('index'))
    return render_template('login-reg.html')

# --- API Routes for Data ---
@app.route('/api/readings/latest')
@login_required
def get_latest_readings():
    conn = None; cursor = None; user_id = session['user_id']; target_device_db_id = request.args.get('device_id', type=int)
    if not target_device_db_id: return jsonify({"error": "Device ID parameter is required."}), 400
    try:
        conn = get_db_connection();
        if not conn: return jsonify({"error": "Database connection failed"}), 500
        cursor = conn.cursor(dictionary=True); cursor.execute("SELECT device_unique_id FROM devices WHERE id = %s AND user_id = %s", (target_device_db_id, user_id)); device = cursor.fetchone()
        if not device: return jsonify({"error": "Device not found or access denied."}), 404
        device_unique_id_to_query = device['device_unique_id']
        sql = "SELECT reading_time, temperature, humidity, device_unique_id FROM readings WHERE device_unique_id = %s ORDER BY reading_time DESC LIMIT 1"
        cursor.execute(sql, (device_unique_id_to_query,)); row = cursor.fetchone(); latest_reading = {}
        if row:
            latest_reading = dict(row)
            if isinstance(latest_reading.get('reading_time'), datetime): latest_reading['reading_time'] = latest_reading['reading_time'].isoformat()
            if isinstance(latest_reading.get('temperature'), Decimal): latest_reading['temperature'] = float(latest_reading['temperature'])
            if isinstance(latest_reading.get('humidity'), Decimal): latest_reading['humidity'] = float(latest_reading['humidity'])
    except Error as e: app.logger.error(f"DB Err latest reading user {user_id}, dev {target_device_db_id}: {e}"); return jsonify({"error": "Failed to fetch latest data"}), 500
    except Exception as e: app.logger.error(f"Unexpected err latest reading: {e}", exc_info=True); return jsonify({"error": "Internal server error"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()
    return jsonify(latest_reading)

@app.route('/api/chartdata')
@login_required
def get_chart_data():
    time_range = request.args.get('range'); start_date_str = request.args.get('start_date'); end_date_str = request.args.get('end_date'); device_db_id = request.args.get('device_id', type=int)
    user_id = session['user_id']
    if not device_db_id: return jsonify({"error": "Device ID parameter is required."}), 400
    app.logger.info(f"Chart data request - User: {user_id}, DeviceDBID: {device_db_id}, Range: {time_range}, Start: {start_date_str}, End: {end_date_str}")
    conn = None; device_unique_id = None
    try:
        conn = get_db_connection();
        if not conn: return jsonify({"error": "Database connection failed"}), 500
        cursor = conn.cursor(dictionary=True); cursor.execute("SELECT device_unique_id FROM devices WHERE id = %s AND user_id = %s", (device_db_id, user_id)); device = cursor.fetchone(); cursor.close()
        if not device: return jsonify({"error": "Device not found or access denied."}), 404
        device_unique_id = device['device_unique_id']
        start_dt_query = None; end_dt_exclusive = None; interval_minutes = 5; now = datetime.now(); today_start = datetime.combine(date.today(), time.min)

        if start_date_str and end_date_str: # Custom Range
            start_dt_query = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_dt_exclusive = datetime.combine(datetime.strptime(end_date_str, '%Y-%m-%d').date(), time.max)
            delta = end_dt_exclusive - start_dt_query
            if delta > timedelta(days=366):
                interval_minutes = 1440
            elif delta > timedelta(days=93):
                interval_minutes = 60 * 6
            elif delta > timedelta(days=31):
                interval_minutes = 60
            elif delta > timedelta(days=7):
                interval_minutes = 30
            elif delta > timedelta(days=1):
                interval_minutes = 15
            else:
                interval_minutes = 5
            app.logger.debug(f"Custom range: {start_dt_query} to {end_dt_exclusive}, Interval: {interval_minutes} min")

        elif time_range: # Relative Range
            end_dt_exclusive = now
            if time_range == 'hour':
                start_dt_query = now - timedelta(hours=1); interval_minutes = 1
            elif time_range == '8hour':
                start_dt_query = now - timedelta(hours=8); interval_minutes = 5
            elif time_range == 'last24h':
                start_dt_query = now - timedelta(hours=24); interval_minutes = 10
            elif time_range == 'past7d':
                start_dt_query = now - timedelta(days=7); interval_minutes = 30
            elif time_range == 'past31d':
                start_dt_query = now - timedelta(days=31); interval_minutes = 60
            elif time_range == 'past365d':
                start_dt_query = now - timedelta(days=365); interval_minutes = 1440
            elif time_range == 'day':
                start_dt_query = today_start; interval_minutes = 5
            elif time_range == 'week':
                start_dt_query = today_start - timedelta(days=now.weekday()); interval_minutes = 30
            elif time_range == 'month':
                start_dt_query = today_start.replace(day=1); interval_minutes = 60
            elif time_range == 'year':
                start_dt_query = today_start.replace(month=1, day=1); interval_minutes = 1440
            else:
                return jsonify({"error": "Invalid time range specified."}), 400
            app.logger.debug(f"Relative range '{time_range}': {start_dt_query} to {end_dt_exclusive}, Interval: {interval_minutes} min")
        else:
             return jsonify({"error": "Missing time range or date parameters."}), 400

        if not isinstance(start_dt_query, datetime) or not isinstance(end_dt_exclusive, datetime): return jsonify({"error": "Internal error determining time range."}), 500
        final_labels, final_temps, final_humids, gaps_identified = fetch_and_process_data(conn, device_unique_id, start_dt_query, end_dt_exclusive, interval_minutes)
        chart_data = { "labels": final_labels, "temperatures": final_temps, "humidities": final_humids, "gaps": gaps_identified }
    except ValueError as ve: app.logger.error(f"Date/value error device {device_db_id}: {ve}"); return jsonify({"error": "Invalid date format or value."}), 400
    except Error as e: app.logger.error(f"DB error chart data device {device_db_id}: {e}"); return jsonify({"error": "Database error processing chart data."}), 500
    except Exception as e: app.logger.error(f"Unexpected error chart data device {device_db_id}: {e}", exc_info=True); return jsonify({"error": "Internal server error."}), 500
    finally:
        if conn and conn.is_connected(): conn.close()
    return jsonify(chart_data)

# --- API Routes for Authentication ---
@app.route('/api/register', methods=['POST'])
def api_register():
    conn = None; cursor = None; name = request.form.get('name'); email = request.form.get('email')
    try:
        password = request.form.get('password'); security_question = request.form.get('security_question'); security_answer = request.form.get('security_answer')
        if not all([name, email, password, security_question, security_answer]): return jsonify({'success': False, 'message': 'Missing required fields.'}), 400
        conn = get_db_connection();
        if not conn: return jsonify({'success': False, 'message': 'Database connection failed.'}), 500
        cursor = conn.cursor(); cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone(): return jsonify({'success': False, 'message': 'Email already registered!'}), 409
        hashed_password = generate_password_hash(password); hashed_security_answer = generate_password_hash(security_answer)
        sql = "INSERT INTO users (name, email, password, security_question, security_answer) VALUES (%s, %s, %s, %s, %s)"
        cursor.execute(sql, (name, email, hashed_password, security_question, hashed_security_answer)); conn.commit()
        app.logger.info(f"User registered successfully: {email}"); return jsonify({'success': True, 'message': 'Registration successful!'}), 201
    except Error as e:
        app.logger.error(f"Database error during registration for {email}: {e}")
        if e.errno == 1062: return jsonify({'success': False, 'message': 'Email already registered!'}), 409
        return jsonify({'success': False, 'message': 'Database error during registration.'}), 500
    except Exception as e: app.logger.error(f"Unexpected error during registration for {email}: {e}", exc_info=True); return jsonify({'success': False, 'message': 'An internal server error occurred.'}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

# Temporary logging
@app.route('/api/login', methods=['POST'])
def api_login():
    app.logger.debug("--- /api/login endpoint CALLED ---") # Log entry
    conn = None; cursor = None; email = request.form.get('email')
    app.logger.debug(f"Login attempt for email: {email}")
    try:
        password = request.form.get('password')
        if not email or not password:
            app.logger.warning("Login failed: Missing email or password.")
            return jsonify({'success': False, 'message': 'Email and password are required.'}), 400

        app.logger.debug("Attempting DB connection for login...")
        conn = get_db_connection();
        if not conn:
            app.logger.error("Login failed: DB connection failed.")
            return jsonify({'success': False, 'message': 'Database connection failed.'}), 500
        app.logger.debug("DB connection successful.")

        cursor = conn.cursor(dictionary=True)
        app.logger.debug(f"Executing user lookup for: {email}")
        cursor.execute("SELECT id, name, email, password FROM users WHERE email = %s", (email,));
        user = cursor.fetchone()
        app.logger.debug(f"User lookup result: {'User found' if user else 'User NOT found'}")

        if user and check_password_hash(user['password'], password):
            app.logger.info(f"Password VALID for {email}. Preparing session.")
            session.clear() # Clear any old session data first
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=7)
            app.logger.debug(f"Session SET before return: {dict(session)}") # Check session content
            # --- Expected return path ---
            app.logger.info(f"Login successful for {email} (ID: {user['id']}). Returning JSON 200.")
            return jsonify({'success': True, 'message': 'Login successful!', 'user': { 'id': user['id'], 'name': user['name'] }}), 200
        else:
            app.logger.warning(f"Failed login attempt for email: {email} - Incorrect email or password.")
            return jsonify({'success': False, 'message': 'Incorrect email or password.'}), 401

    except Error as e:
        app.logger.error(f"Database error during login for {email}: {e}")
        return jsonify({'success': False, 'message': 'Database error during login.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error during login for {email}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': 'An internal server error occurred.'}), 500
    finally:
        if cursor:
            app.logger.debug("Closing DB cursor.")
            cursor.close()
        if conn and conn.is_connected():
            app.logger.debug("Closing DB connection.")
            conn.close()
        app.logger.debug("--- /api/login endpoint FINISHED ---") # Log exit

@app.route('/api/logout')
def api_logout():
    user_name = session.get('user_name', 'Unknown'); user_id = session.get('user_id')
    session.clear(); app.logger.info(f"User logged out: {user_name} (ID: {user_id})")
    return redirect(url_for('login_page'))

@app.route('/api/session-check')
def api_session_check():
    try:
        is_logged_in = session.get('logged_in', False)
        
        # Build the response dictionary
        user_details = {'logged_in': bool(is_logged_in)}
        
        if is_logged_in:
            user_details['user_id'] = session.get('user_id')
            user_details['user_name'] = session.get('user_name')
        else:
            user_details['user_id'] = None
            user_details['user_name'] = None
        
        return jsonify(user_details)
    except Exception as e:
        app.logger.error(f"Session check error: {str(e)}")
        return jsonify({'logged_in': False, 'error': 'Session check failed'}), 500

@app.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    conn = None; cursor = None; action = request.form.get('action'); email = request.form.get('email')
    app.logger.info(f"Forgot password request. Action: {action}, Email: {email}")
    try:
        conn = get_db_connection();
        if not conn: return jsonify({'success': False, 'message': 'DB connection failed.'}), 500
        if action == 'verifyEmail':
            if not email: return jsonify({'success': False, 'message': 'Email required.'}), 400
            cursor = conn.cursor(dictionary=True); cursor.execute("SELECT security_question FROM users WHERE email = %s", (email,)); user = cursor.fetchone(); cursor.close()
            if user: q_map={'pet':"What was your first pet's name?",'school':"What was the name of your first school?",'city':"What city were you born in?",'maiden':"What was your mother's maiden name?"}; q_text=q_map.get(user['security_question'],"Unknown question."); return jsonify({'success': True, 'security_question': q_text})
            else: return jsonify({'success': False, 'message': 'Email not found.'}), 404
        elif action == 'verifyAnswer':
            security_answer = request.form.get('security_answer');
            if not email or not security_answer: return jsonify({'success': False, 'message': 'Email/answer required.'}), 400
            cursor = conn.cursor(dictionary=True); cursor.execute("SELECT security_answer FROM users WHERE email = %s", (email,)); user = cursor.fetchone(); cursor.close()
            if user and check_password_hash(user['security_answer'], security_answer): return jsonify({'success': True, 'message': 'Answer verified.'})
            else: return jsonify({'success': False, 'message': 'Incorrect answer/email.'}), 401
        elif action == 'resetPassword':
            new_password = request.form.get('new_password');
            if not email or not new_password: return jsonify({'success': False, 'message': 'Email/new password required.'}), 400
            if len(new_password) < 8: return jsonify({'success': False, 'message': 'Password >= 8 chars.'}), 400
            hashed_password = generate_password_hash(new_password); update_cursor = conn.cursor()
            update_sql = "UPDATE users SET password = %s WHERE email = %s"; update_cursor.execute(update_sql, (hashed_password, email)); rows = update_cursor.rowcount; conn.commit(); update_cursor.close()
            if rows > 0: return jsonify({'success': True, 'message': 'Password reset successful.'})
            else: return jsonify({'success': False, 'message': 'User not found during reset.'}), 404
        else: return jsonify({'success': False, 'message': 'Invalid action.'}), 400
    except Error as e: app.logger.error(f"DB error forgot pw action '{action}' email {email}: {e}"); return jsonify({'success': False, 'message': 'DB error during recovery.'}), 500
    except Exception as e: app.logger.error(f"Unexpected error forgot pw action '{action}' email {email}: {e}", exc_info=True); return jsonify({'success': False, 'message': 'Internal server error.'}), 500
    finally:
        if conn and conn.is_connected(): conn.close()


# --- API Routes for Device Management ---
@app.route('/api/user/devices', methods=['GET'])
@login_required
def get_user_devices():
    conn = None; cursor = None; user_id = session.get('user_id'); app.logger.info(f"Fetching devices for user: {user_id}")
    try:
        conn = get_db_connection();
        if not conn: return jsonify({'success': False, 'message': 'DB connection failed.'}), 500
        cursor = conn.cursor(dictionary=True); cursor.execute("SELECT id, device_unique_id, device_name, min_temp_threshold, max_temp_threshold FROM devices WHERE user_id = %s ORDER BY created_at ASC", (user_id,)); devices = cursor.fetchall()
        for device in devices:
            if device.get('min_temp_threshold') is not None: device['min_temp_threshold'] = float(device['min_temp_threshold'])
            if device.get('max_temp_threshold') is not None: device['max_temp_threshold'] = float(device['max_temp_threshold'])
        return jsonify({'success': True, 'devices': devices})
    except Error as e: app.logger.error(f"DB error fetching devices user {user_id}: {e}"); return jsonify({'success': False, 'message': 'DB error fetching devices.'}), 500
    except Exception as e: app.logger.error(f"Unexpected error fetching devices user {user_id}: {e}", exc_info=True); return jsonify({'success': False, 'message': 'Internal server error.'}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

@app.route('/api/user/devices/link', methods=['POST'])
@login_required
def link_device():
    conn = None; cursor = None; user_id = session.get('user_id'); device_unique_id = request.form.get('device_unique_id'); device_name = request.form.get('device_name')
    app.logger.info(f"Linking device '{device_unique_id}' (Name: {device_name}) user: {user_id}")
    if not device_unique_id: return jsonify({'success': False, 'message': 'Device ID required.'}), 400
    if len(device_unique_id) > 255 or len(device_unique_id) < 3: return jsonify({'success': False, 'message': 'Invalid Device ID format.'}), 400
    try:
        conn = get_db_connection();
        if not conn: return jsonify({'success': False, 'message': 'DB connection failed.'}), 500
        cursor = conn.cursor(dictionary=True); cursor.execute("SELECT id, user_id FROM devices WHERE device_unique_id = %s", (device_unique_id,)); existing_device = cursor.fetchone(); cursor.close()
        if existing_device:
            if existing_device['user_id'] == user_id: return jsonify({'success': False, 'message': 'Device already linked.'}), 409
            else: return jsonify({'success': False, 'message': 'Device registered to another user.'}), 403
        insert_sql = "INSERT INTO devices (user_id, device_unique_id, device_name, min_temp_threshold, max_temp_threshold) VALUES (%s, %s, %s, NULL, NULL)"
        insert_cursor = conn.cursor(); insert_cursor.execute(insert_sql, (user_id, device_unique_id, device_name if device_name else None)); new_device_id = insert_cursor.lastrowid; conn.commit(); insert_cursor.close()
        app.logger.info(f"Linked device '{device_unique_id}' (DB ID: {new_device_id}) user {user_id}.")
        fetch_cursor = conn.cursor(dictionary=True); fetch_cursor.execute("SELECT id, device_unique_id, device_name, min_temp_threshold, max_temp_threshold FROM devices WHERE id = %s", (new_device_id,)); new_device_data = fetch_cursor.fetchone(); fetch_cursor.close()
        if new_device_data:
             if new_device_data.get('min_temp_threshold') is not None: new_device_data['min_temp_threshold'] = float(new_device_data['min_temp_threshold'])
             if new_device_data.get('max_temp_threshold') is not None: new_device_data['max_temp_threshold'] = float(new_device_data['max_temp_threshold'])
        else: return jsonify({'success': True, 'message': 'Device linked, but failed retrieve details.'}), 201
        return jsonify({'success': True, 'message': 'Device linked successfully!', 'device': new_device_data}), 201
    except Error as e:
        app.logger.error(f"DB error linking device '{device_unique_id}' user {user_id}: {e}")
        if e.errno == 1062: return jsonify({'success': False, 'message': 'Device ID already exists.'}), 409
        return jsonify({'success': False, 'message': 'DB error during linking.'}), 500
    except Exception as e: app.logger.error(f"Unexpected error linking device '{device_unique_id}' user {user_id}: {e}", exc_info=True); return jsonify({'success': False, 'message': 'Internal server error.'}), 500
    finally:
        if conn and conn.is_connected(): conn.close()

@app.route('/api/user/devices/<int:device_db_id>/settings', methods=['PUT'])
@login_required
def update_device_settings(device_db_id):
    conn = None; cursor = None; user_id = session.get('user_id')
    try:
        data = request.get_json();
        if not data: return jsonify({'success': False, 'message': 'JSON data expected.'}), 400
        app.logger.info(f"Update settings device {device_db_id} user {user_id}. Data: {data}")
        update_fields = {}; params = []

        # Handle device name
        if 'device_name' in data:
            update_fields['device_name'] = '%s'
            params.append(data['device_name'])

        # Handle min_temp
        if 'min_temp' in data:
            val = data['min_temp']
            if val is None or str(val).strip() == '':
                update_fields['min_temp_threshold'] = '%s'
                params.append(None)
            else:
                try:
                    f_val = float(val)
                    update_fields['min_temp_threshold'] = '%s'
                    params.append(f_val)
                except (ValueError, TypeError):
                    return jsonify({'success': False, 'message': 'Invalid min temp.'}), 400

        # Handle max_temp
        if 'max_temp' in data:
            val = data['max_temp']
            if val is None or str(val).strip() == '':
                update_fields['max_temp_threshold'] = '%s'
                params.append(None)
            else:
                try:
                    f_val = float(val)
                    update_fields['max_temp_threshold'] = '%s'
                    params.append(f_val)
                except (ValueError, TypeError):
                    return jsonify({'success': False, 'message': 'Invalid max temp.'}), 400

        # Simplified min/max check
        final_min_to_set = None; min_idx = list(update_fields.keys()).index('min_temp_threshold') if 'min_temp_threshold' in update_fields else -1;
        if min_idx != -1: final_min_to_set = params[min_idx]
        final_max_to_set = None; max_idx = list(update_fields.keys()).index('max_temp_threshold') if 'max_temp_threshold' in update_fields else -1;
        if max_idx != -1: final_max_to_set = params[max_idx]
        check_min = final_min_to_set; check_max = final_max_to_set
        if isinstance(check_min, (int, float)) and isinstance(check_max, (int, float)) and check_min >= check_max:
             return jsonify({'success': False, 'message': 'Min temp must be less than max temp.'}), 400

        if not update_fields: return jsonify({'success': False, 'message': 'No valid settings provided.'}), 400

        conn = get_db_connection();
        if not conn: return jsonify({'success': False, 'message': 'DB connection failed.'}), 500

        cursor = conn.cursor()
        set_clause = ", ".join([f"{key} = %s" for key in update_fields.keys()]) # Use % placeholders
        params.append(device_db_id); params.append(user_id) # Ensure user ownership

        sql = f"UPDATE devices SET {set_clause} WHERE id = %s AND user_id = %s"
        app.logger.debug(f"Executing update: {sql} with params: {tuple(params)}")
        cursor.execute(sql, tuple(params)); rows_affected = cursor.rowcount

        if rows_affected == 0:
             return jsonify({'success': False, 'message': 'Device not found or permission denied.'}), 404 # or 403

        conn.commit()
        app.logger.info(f"Settings updated device {device_db_id} user {user_id}.")
        return jsonify({'success': True, 'message': 'Settings updated successfully!'})

    except Error as e: app.logger.error(f"DB error update settings device {device_db_id}: {e}"); return jsonify({'success': False, 'message': 'DB error updating settings.'}), 500
    except (ValueError, TypeError) as e: app.logger.error(f"Value/Type error update settings: {e}"); return jsonify({'success': False, 'message': 'Invalid value provided.'}), 400
    except Exception as e: app.logger.error(f"Unexpected error update settings device {device_db_id}: {e}", exc_info=True); return jsonify({'success': False, 'message': 'Internal server error.'}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/user/devices/<int:device_db_id>/unlink', methods=['DELETE'])
@login_required
def unlink_device(device_db_id):
    conn = None; cursor = None; user_id = session.get('user_id'); app.logger.info(f"Unlink device {device_db_id} user {user_id}")
    try:
        conn = get_db_connection();
        if not conn: return jsonify({'success': False, 'message': 'DB connection failed.'}), 500
        cursor = conn.cursor()
        sql = "DELETE FROM devices WHERE id = %s AND user_id = %s"; cursor.execute(sql, (device_db_id, user_id)); rows_affected = cursor.rowcount
        if rows_affected == 0: return jsonify({'success': False, 'message': 'Device not found or permission denied.'}), 404
        conn.commit(); app.logger.info(f"Device {device_db_id} unlinked user {user_id}."); return jsonify({'success': True, 'message': 'Device unlinked successfully.'})
    except Error as e:
        app.logger.error(f"DB error unlinking device {device_db_id}: {e}")
        if e.errno == 1451: return jsonify({'success': False, 'message': 'Cannot unlink device with readings.'}), 409
        return jsonify({'success': False, 'message': 'DB error unlinking device.'}), 500
    except Exception as e: app.logger.error(f"Unexpected error unlinking device {device_db_id}: {e}", exc_info=True); return jsonify({'success': False, 'message': 'Internal server error.'}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

# --- API Route for Receiving Device Data ---
@app.route('/api/device/readings', methods=['POST'])
def receive_device_readings():
    data = request.get_json();
    if not data: return jsonify({"error": "JSON data expected."}), 400
    device_uid = data.get('device_unique_id'); temp = data.get('temperature'); humid = data.get('humidity')
    if not device_uid or temp is None or humid is None: return jsonify({"error": "Missing required fields."}), 400
    conn = None; cursor = None; insert_cursor = None
    try:
        conn = get_db_connection();
        if not conn: return jsonify({"error": "DB connection failed."}), 500
        cursor = conn.cursor(dictionary=True); cursor.execute("SELECT id FROM devices WHERE device_unique_id = %s", (device_uid,)); device = cursor.fetchone(); cursor.close()
        if not device: app.logger.warning(f"Reading from unknown device: {device_uid}"); return jsonify({"error": "Device ID not registered."}), 403
        try: temp_float = float(temp); humid_float = float(humid)
        except (ValueError,TypeError): return jsonify({"error": "Invalid temp/humid value."}), 400
        reading_time = datetime.now(); sql = "INSERT INTO readings (device_unique_id, reading_time, temperature, humidity) VALUES (%s, %s, %s, %s)"
        insert_cursor = conn.cursor(); insert_cursor.execute(sql, (device_uid, reading_time, temp_float, humid_float)); conn.commit()
        app.logger.info(f"Stored reading from device {device_uid}"); return jsonify({"success": True, "message": "Reading stored."}), 201
    except Error as e: app.logger.error(f"DB error storing reading device {device_uid}: {e}"); return jsonify({"error": "DB error storing reading."}), 500
    except Exception as e: app.logger.error(f"Unexpected error storing reading device {device_uid}: {e}", exc_info=True); return jsonify({"error": "Internal server error."}), 500
    finally:
        if insert_cursor: insert_cursor.close()
        if conn and conn.is_connected(): conn.close()

# --- API Route for Device Settings ---
""" Connects to the database and queries the devices table for the min_temp_threshold and max_temp_threshold columns 
where the device_unique_id matches the one provided in the URL. """
#
@app.route('/api/device/settings/<string:device_unique_id>', methods=['GET'])
def get_device_settings(device_unique_id):
    """
    API endpoint for a device to fetch its own settings (thresholds).
    Accessed via GET /api/device/settings/<device_unique_id>
    """
    if not device_unique_id:
        app.logger.warning("Attempt to fetch settings with empty device ID.")
        return jsonify({"error": "Device unique ID is required."}), 400

    conn = None
    cursor = None
    app.logger.info(f"Device settings request received for ID: {device_unique_id}")

    try:
        conn = get_db_connection()
        if not conn:
            app.logger.error(f"Failed to get DB connection for settings request (Device: {device_unique_id})")
            return jsonify({"error": "Database connection failed"}), 500

        cursor = conn.cursor(dictionary=True)
        sql = "SELECT min_temp_threshold, max_temp_threshold FROM devices WHERE device_unique_id = %s"
        cursor.execute(sql, (device_unique_id,))
        device_settings = cursor.fetchone()

        if device_settings:
            min_temp = device_settings.get('min_temp_threshold')
            max_temp = device_settings.get('max_temp_threshold')

            settings_data = {
                'min_temp_threshold': float(min_temp) if min_temp is not None else None,
                'max_temp_threshold': float(max_temp) if max_temp is not None else None
            }
            app.logger.info(f"Found settings for device {device_unique_id}: {settings_data}")
            return jsonify(settings_data), 200
        else:
            app.logger.warning(f"Settings request failed: Device ID {device_unique_id} not found in database.")
            return jsonify({"error": "Device not found"}), 404

    except Error as e:
        app.logger.error(f"Database error fetching settings for device {device_unique_id}: {e}")
        return jsonify({"error": "Database error fetching settings."}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error fetching settings for device {device_unique_id}: {e}", exc_info=True)
        return jsonify({"error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
            app.logger.debug("DB connection closed for settings request.")

# --- Run the App ---
if __name__ == '__main__':
    app.logger.info("Starting Flask development server.")
    app.run(debug=True, host='0.0.0.0', port=5000)
    
