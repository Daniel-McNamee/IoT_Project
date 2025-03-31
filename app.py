# /home/DanDev/terrarium_webapp/app.py
from flask import Flask, render_template, jsonify # Added jsonify for API later
import mysql.connector
from mysql.connector import Error
import os # Needed for environment variables (Not currently in use)
import datetime # To handle potential datetime conversion

app = Flask(__name__) # Create the Flask app instance

# --- Database Configuration ---
DB_HOST = 'localhost'
DB_USER = 'terrarium_user' # User with SELECT privileges
DB_PASSWORD = 'Life4588'
DB_NAME = 'terrarium_data'

# Basic check - Flask app won't work without DB access here
if not DB_PASSWORD:
    app.logger.error("FATAL ERROR: DB_MONITOR_PASSWORD environment variable not set!")

def get_db_connection():
    """Connects to the database."""
    conn = None # Initialize conn to None
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=5
        )
        if conn.is_connected():
            app.logger.info("DB connection successful for web request.")
            return conn
    except Error as e:
        app.logger.error(f"Error connecting to DB for web app: {e}")
        if conn and conn.is_connected(): # Close if connection object exists but failed later
             conn.close()
    return None # Return None if connection failed

# --- Routes ---
@app.route('/') # The main page URL
def index():
    """Renders the main HTML page."""
    # Add data fetching here later
    return render_template('index.html', title='Terrarium Monitor')

# Simple API endpoint to test data fetching
@app.route('/api/readings/latest')
def get_latest_readings():
    """API endpoint to get the last N readings."""
    conn = get_db_connection()
    if not conn:
         return jsonify({"error": "Database connection failed"}), 500

    cursor = None # Initialize cursor
    readings_data = []
    try:
        cursor = conn.cursor(dictionary=True) # dictionary=True is very useful
        # Get the last 10 readings for testing
        cursor.execute("SELECT reading_time, temperature, humidity FROM readings ORDER BY reading_time DESC LIMIT 10")
        readings_data = cursor.fetchall()
        # Convert datetime objects to strings for JSON compatibility
        for row in readings_data:
            if isinstance(row.get('reading_time'), datetime.datetime):
                row['reading_time'] = row['reading_time'].isoformat()

    except Error as e:
        app.logger.error(f"Error fetching latest readings: {e}")
        return jsonify({"error": "Failed to fetch data"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

    return jsonify(readings_data) # Return data as JSON

@app.route('/api/chartdata/recent')
def get_recent_chart_data():
    """API endpoint to fetch data formatted for Chart.js (last hour)."""
    conn = get_db_connection()
    if not conn:
         return jsonify({"error": "Database connection failed"}), 500

    cursor = None
    chart_data = {
        "labels": [],
        "temperatures": [],
        "humidities": []
    }
    try:
        cursor = conn.cursor(dictionary=True)
        # Query for readings within the last hour, ordered
        query = """
            SELECT reading_time, temperature, humidity
            FROM readings
            WHERE reading_time >= NOW() - INTERVAL 1 HOUR
            ORDER BY reading_time ASC
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        for row in rows:
            # Format timestamp for display (HH:MM:SS)
            if isinstance(row.get('reading_time'), datetime.datetime):
                 time_label = row['reading_time'].strftime('%H:%M:%S')
                 chart_data["labels"].append(time_label)
            else:
                 chart_data["labels"].append(None)

            # Append sensor data (handle potential NULLs from DB)
            chart_data["temperatures"].append(row.get('temperature'))
            chart_data["humidities"].append(row.get('humidity'))

        app.logger.info(f"Fetched {len(rows)} rows for recent chart data.")

    except Error as e:
        app.logger.error(f"Error fetching recent chart data: {e}")
        return jsonify({"error": "Failed to fetch chart data"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

    return jsonify(chart_data) # Return structured data

# --- Run the App ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

