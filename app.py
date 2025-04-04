# /home/DanDev/terrarium_webapp/app.py
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import mysql.connector
from mysql.connector import Error
import os
from datetime import datetime, date, timedelta, time
import math
from collections import defaultdict
from decimal import Decimal
# New imports for user authentication
from werkzeug.security import generate_password_hash, check_password_hash
import secrets # For generating a secret key if needed
import logging

app = Flask(__name__)

# --- Logging Configuration ---
# Configure logging level
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

# --- Get interval key ---
def get_interval_key(reading_time, interval_minutes):
    if interval_minutes == 1440: # Daily
        interval_start_dt = datetime.combine(reading_time.date(), time.min)
        return interval_start_dt.strftime('%Y-%m-%d %H:%M')
    elif interval_minutes == 60: # Hourly
        interval_start_dt = reading_time.replace(minute=0, second=0, microsecond=0)
        return interval_start_dt.strftime('%Y-%m-%d %H:%M')
    elif interval_minutes >= 1 and interval_minutes < 60: # Minutely intervals
        minutes_past_hour = reading_time.minute
        interval_start_minute = (minutes_past_hour // interval_minutes) * interval_minutes
        interval_start_dt = reading_time.replace(minute=interval_start_minute, second=0, microsecond=0)
        return interval_start_dt.strftime('%Y-%m-%d %H:%M')
    else:
        app.logger.warning(f"Unsupported interval_minutes for key generation: {interval_minutes}")
        return reading_time.strftime('%Y-%m-%d %H:%M')

# --- Fetch and process data ---
def fetch_and_process_data(conn, start_dt_query, end_dt_exclusive, interval_minutes):
    cursor = None; final_labels = []; final_temps = []; final_humids = []; gaps_identified = []
    try:
        where_clause = "WHERE reading_time >= %s AND reading_time < %s"
        query_params = (start_dt_query, end_dt_exclusive)
        query = f"SELECT reading_time, temperature, humidity FROM readings {where_clause} ORDER BY reading_time ASC"
        app.logger.debug(f"Executing SQL: {query} with params: {query_params}")
        cursor = conn.cursor(dictionary=True); cursor.execute(query, query_params); rows = cursor.fetchall()
        app.logger.info(f"Fetched {len(rows)} raw data points between {start_dt_query} and {end_dt_exclusive} for aggregation.")
        aggregated_data = defaultdict(lambda: {'sum_temp': 0.0, 'sum_humid': 0.0, 'count': 0})
        for row in rows:
            temp_db = row.get('temperature'); humid_db = row.get('humidity'); reading_time = row.get('reading_time')
            if temp_db is None or humid_db is None or not isinstance(reading_time, datetime): continue
            try:
                temp = float(temp_db); humid = float(humid_db) # Convert Decimal to float
            except (ValueError, TypeError) as conversion_error:
                app.logger.warning(f"Could not convert DB values to float: {conversion_error}. Skipping row.")
                continue
            interval_key = get_interval_key(reading_time, interval_minutes)
            aggregated_data[interval_key]['sum_temp'] += temp
            aggregated_data[interval_key]['sum_humid'] += humid
            aggregated_data[interval_key]['count'] += 1
        averaged_data_map = {}
        for key, data in aggregated_data.items():
            if data['count'] > 0:
                avg_temp = data['sum_temp'] / data['count']; avg_humid = data['sum_humid'] / data['count']
                averaged_data_map[key] = {'temp': round(avg_temp, 2), 'humid': round(avg_humid, 2)}
        current_dt = start_dt_query; interval = timedelta(minutes=interval_minutes)
        label_format_final = '%Y-%m-%d %H:%M'; in_gap = False; gap_start_label = None
        app.logger.debug(f"Timeline generation starting from {current_dt} with {interval_minutes} min interval up to {end_dt_exclusive}")
        while current_dt < end_dt_exclusive:
            current_label_key = get_interval_key(current_dt, interval_minutes); current_label_display = current_label_key
            final_labels.append(current_label_display)
            if current_label_key in averaged_data_map:
                data_point = averaged_data_map[current_label_key]; final_temps.append(data_point['temp']); final_humids.append(data_point['humid'])
                if in_gap:
                    last_null_dt = current_dt - interval; last_null_label = get_interval_key(last_null_dt, interval_minutes)
                    gaps_identified.append({"start": gap_start_label, "end": last_null_label}); in_gap = False; gap_start_label = None
            else:
                final_temps.append(None); final_humids.append(None)
                if not in_gap: in_gap = True; gap_start_label = current_label_display
            current_dt += interval
        if in_gap:
            last_null_dt = current_dt - interval; last_null_label = get_interval_key(last_null_dt, interval_minutes)
            gaps_identified.append({"start": gap_start_label, "end": last_null_label})
    except Error as e: app.logger.error(f"Database error during fetch/process: {e}"); raise
    except Exception as e: app.logger.error(f"Unexpected error during fetch/process: {e}", exc_info=True); raise
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
    if 'logged_in' in session:
         app.logger.info("User already logged in, redirecting from /login to index.")
         return redirect(url_for('index'))
    return render_template('login-reg.html')

# --- API Routes for Data ---

@app.route('/api/readings/latest')
def get_latest_readings():
    # TODO: Add login check later
    # if not session.get('logged_in'):
    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500
    cursor = None; readings_data = []
    try:
        cursor = conn.cursor(dictionary=True); cursor.execute("SELECT reading_time, temperature, humidity FROM readings ORDER BY reading_time DESC LIMIT 10"); readings_data = cursor.fetchall()
        for row in readings_data:
             if isinstance(row.get('reading_time'), datetime):
                row['reading_time'] = row['reading_time'].isoformat()
             if isinstance(row.get('temperature'), Decimal):
                 row['temperature'] = float(row['temperature'])
             if isinstance(row.get('humidity'), Decimal):
                 row['humidity'] = float(row['humidity'])
    except Error as e: app.logger.error(f"Error fetching latest readings: {e}"); return jsonify({"error": "Failed to fetch data"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected():
            app.logger.debug("Closing DB connection for /api/readings/latest.")
            conn.close()
    return jsonify(readings_data)


@app.route('/api/chartdata')
def get_chart_data():
    # TODO: Add login check later
    # if not session.get('logged_in'):
    time_range = request.args.get('range')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    app.logger.info(f"Chart data request - Range: {time_range}, Start: {start_date_str}, End: {end_date_str}")

    conn = get_db_connection()
    if not conn: return jsonify({"error": "Database connection failed"}), 500

    start_dt_query = None; end_dt_exclusive = None; interval_minutes = 5
    agg_type = "Unknown"

    try:
        now = datetime.now(); today_start = datetime.combine(date.today(), time.min)
        if start_date_str and end_date_str: # Custom Range
            # Custom range logic
        elif time_range: # Relative Range
            # Relative range logic
        else:
             app.logger.error("No valid range or date parameters provided.")
             return jsonify({"error": "Missing time range or date parameters."}), 400

        final_labels, final_temps, final_humids, gaps_identified = fetch_and_process_data(
            conn, start_dt_query, end_dt_exclusive, interval_minutes
        )
        chart_data = { "labels": final_labels, "temperatures": final_temps, "humidities": final_humids, "gaps": gaps_identified }

    except Error as e:
        app.logger.error(f"Database error processing chart data: {e}")
        return jsonify({"error": "Failed to fetch chart data due to database error"}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error processing chart data request: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred"}), 500
    finally:
        if conn and conn.is_connected():
            app.logger.debug("Closing DB connection for /api/chartdata.")
            conn.close()
    return jsonify(chart_data)


# --- API Routes for Authentication ---

@app.route('/api/register', methods=['POST'])
def api_register():
    conn = None
    cursor = None
    name = request.form.get('name') # Get name early for logging
    email = request.form.get('email') # Get email early for logging
    try:
        password = request.form.get('password')
        security_question = request.form.get('security_question')
        security_answer = request.form.get('security_answer')

        if not all([name, email, password, security_question, security_answer]):
            app.logger.warning(f"Registration attempt failed for {email}: Missing fields.")
            return jsonify({'success': False, 'message': 'Missing required fields.'}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Database connection failed.'}), 500

        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            app.logger.warning(f"Registration attempt failed for {email}: Email already exists.")
            return jsonify({'success': False, 'message': 'Email already registered!'}), 409

        hashed_password = generate_password_hash(password)
        hashed_security_answer = generate_password_hash(security_answer)

        sql = "INSERT INTO users (name, email, password, security_question, security_answer) VALUES (%s, %s, %s, %s, %s)"
        cursor.execute(sql, (name, email, hashed_password, security_question, hashed_security_answer))
        conn.commit()

        app.logger.info(f"User registered successfully: {email}")
        return jsonify({'success': True, 'message': 'Registration successful!'}), 201

    except Error as e:
        app.logger.error(f"Database error during registration for {email}: {e}")
        return jsonify({'success': False, 'message': 'Database error during registration.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error during registration for {email}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': 'An internal server error occurred.'}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected():
            app.logger.debug("Closing DB connection for /api/register.")
            conn.close()

@app.route('/api/login', methods=['POST'])
def api_login():
    conn = None
    cursor = None
    email = request.form.get('email') # Get email early for logging
    try:
        password = request.form.get('password')

        if not email or not password:
            app.logger.warning(f"Login attempt failed for {email}: Missing email or password.")
            return jsonify({'success': False, 'message': 'Email and password are required.'}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Database connection failed.'}), 500

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, email, password FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user and check_password_hash(user['password'], password):
            session.clear()
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=7)

            app.logger.info(f"User logged in successfully: {email} (ID: {user['id']})")
            app.logger.debug(f"Session data after login: {session}")
            return jsonify({
                'success': True,
                'message': 'Login successful!',
                'user': { 'id': user['id'], 'name': user['name'] }
            }), 200
        else:
            app.logger.warning(f"Failed login attempt for email: {email}")
            return jsonify({'success': False, 'message': 'Incorrect email or password!'}), 401

    except Error as e:
        app.logger.error(f"Database error during login for {email}: {e}")
        return jsonify({'success': False, 'message': 'Database error during login.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error during login for {email}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': 'An internal server error occurred.'}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected():
            app.logger.debug("Closing DB connection for /api/login.")
            conn.close()

@app.route('/api/logout')
def api_logout():
    user_name = session.get('user_name', 'Unknown')
    session.clear()
    app.logger.info(f"User logged out: {user_name}")
    return redirect(url_for('login_page'))

@app.route('/api/session-check')
def api_session_check():
    is_logged_in = session.get('logged_in', False)
    user_details = {
        'logged_in': is_logged_in,
        'user_id': session.get('user_id') if is_logged_in else None,
        'user_name': session.get('user_name') if is_logged_in else None
    }
    # Avoid logging sensitive session details every check unless necessary
    app.logger.debug(f"Session check requested. Logged in: {is_logged_in}")
    return jsonify(user_details)

# --- Forgot Password API ---
@app.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    conn = None
    cursor = None
    action = request.form.get('action')
    email = request.form.get('email')
    app.logger.info(f"Forgot password request received. Action: {action}, Email: {email}")

    try:
        conn = get_db_connection()
        if not conn: return jsonify({'success': False, 'message': 'Database connection failed.'}), 500
        cursor = conn.cursor(dictionary=True)

        if action == 'verifyEmail':
            if not email: return jsonify({'success': False, 'message': 'Email is required.'}), 400
            cursor.execute("SELECT security_question FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            if user:
                 # Map question key to full text
                 question_map = {
                    'pet': "What was your first pet's name?",
                    'school': "What was the name of your first school?",
                    'city': "What city were you born in?",
                    'maiden': "What was your mother's maiden name?",
                 }
                 question_text = question_map.get(user['security_question'], "Unknown security question.") # Default if key not found
                 app.logger.info(f"Forgot password step 1 successful for {email}.")
                 return jsonify({'success': True, 'security_question': question_text})
            else:
                 app.logger.warning(f"Forgot password step 1 failed for {email}: Email not found.")
                 return jsonify({'success': False, 'message': 'Email not found.'}), 404

        elif action == 'verifyAnswer':
            security_answer = request.form.get('security_answer')
            if not email or not security_answer: return jsonify({'success': False, 'message': 'Email and answer are required.'}), 400

            cursor.execute("SELECT security_answer FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            if user and check_password_hash(user['security_answer'], security_answer):
                 app.logger.info(f"Forgot password step 2 successful for {email}.")
                 return jsonify({'success': True, 'message': 'Security answer verified.'})
            else:
                 app.logger.warning(f"Forgot password step 2 failed for {email}: Incorrect answer or email not found.")
                 return jsonify({'success': False, 'message': 'Incorrect security answer or email.'}), 401

        elif action == 'resetPassword':
            new_password = request.form.get('new_password')
            if not email or not new_password: return jsonify({'success': False, 'message': 'Email and new password are required.'}), 400
            # TODO: Add password complexity checks

            hashed_password = generate_password_hash(new_password)
            update_sql = "UPDATE users SET password = %s WHERE email = %s"
            update_cursor = conn.cursor() # Need a separate cursor if using dictionary cursor above for SELECT
            update_cursor.execute(update_sql, (hashed_password, email))
            conn.commit()
            update_cursor.close()

            if update_cursor.rowcount > 0:
                 app.logger.info(f"Forgot password step 3 successful for {email}: Password reset.")
                 return jsonify({'success': True, 'message': 'Password has been reset successfully.'})
            else:
                 app.logger.error(f"Forgot password step 3 failed for {email}: User not found during update.")
                 return jsonify({'success': False, 'message': 'Failed to reset password. User not found.'}), 404

        else:
            app.logger.warning(f"Forgot password request received with invalid action: {action}")
            return jsonify({'success': False, 'message': 'Invalid action.'}), 400

    except Error as e:
        app.logger.error(f"Database error during forgot password action {action} for {email}: {e}")
        return jsonify({'success': False, 'message': 'Database error during password recovery.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error during forgot password action {action} for {email}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': 'An internal server error occurred.'}), 500
    finally:
        if cursor: cursor.close() # Close dictionary cursor
        if conn and conn.is_connected():
            app.logger.debug("Closing DB connection for /api/forgot-password.")
            conn.close()


# --- Run the App ---
if __name__ == '__main__':
    app.logger.info("Starting Flask development server.")
    app.run(debug=True, host='0.0.0.0', port=5000)

