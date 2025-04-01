# /home/DanDev/terrarium_webapp/app.py
from flask import Flask, render_template, jsonify, request
import mysql.connector
from mysql.connector import Error
import os
from datetime import datetime, date, timedelta

app = Flask(__name__) # Create the Flask app instance

# --- Database Configuration ---
DB_HOST = 'localhost'
DB_USER = 'terrarium_user' # User with SELECT privileges
DB_PASSWORD = 'Life4588'
DB_NAME = 'terrarium_data'

def get_db_connection():
    """Connects to the database."""
    conn = None
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
        if conn and conn.is_connected():
             conn.close()
    return None

# --- Routes ---
@app.route('/')
def index():
    """Renders the main HTML page."""
    return render_template('index.html', title='Terrarium Monitor')

@app.route('/api/readings/latest')
def get_latest_readings():
    """API endpoint to get the last N readings (for testing)."""
    conn = get_db_connection()
    if not conn:
         return jsonify({"error": "Database connection failed"}), 500

    cursor = None
    readings_data = []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT reading_time, temperature, humidity FROM readings ORDER BY reading_time DESC LIMIT 10")
        readings_data = cursor.fetchall()
        for row in readings_data:
            if isinstance(row.get('reading_time'), datetime):
                row['reading_time'] = row['reading_time'].isoformat() # Use ISO format for consistency

    except Error as e:
        app.logger.error(f"Error fetching latest readings: {e}")
        return jsonify({"error": "Failed to fetch data"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

    return jsonify(readings_data)

# --- Consolidated Chart Data Endpoint ---
@app.route('/api/chartdata')
def get_chart_data():
    """API endpoint to fetch data formatted for Chart.js based on time range or custom dates."""
    # Get parameters from query string
    time_range = request.args.get('range')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    app.logger.info(f"Request received - Range: {time_range}, Start: {start_date_str}, End: {end_date_str}")

    conn = get_db_connection()
    if not conn:
         return jsonify({"error": "Database connection failed"}), 500

    cursor = None
    chart_data = {
        "labels": [],
        "temperatures": [],
        "humidities": []
    }
    query_params = None # Initialize query parameters tuple/list
    where_clause = ""
    label_format = '%H:%M:%S' # Default format
    query_base = "SELECT reading_time, temperature, humidity FROM readings "
    query_order = " ORDER BY reading_time ASC"
    # Optional: Limit for very long raw data ranges if needed before aggregation
    # query_limit = " LIMIT 2000" # Example limit

    try:
        # --- Logic Branch: Custom Date Range ---
        if start_date_str and end_date_str:
            app.logger.debug("Processing custom date range request.")
            try:
                start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                app.logger.warning(f"Invalid date format received: Start='{start_date_str}', End='{end_date_str}'")
                return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

            # Validation
            if end_date_obj < start_date_obj:
                app.logger.warning(f"Invalid date range: End date '{end_date_str}' is before start date '{start_date_str}'")
                return jsonify({"error": "End date cannot be before start date."}), 400

            duration_days = (end_date_obj - start_date_obj).days
            if duration_days > 365:
                 app.logger.warning(f"Date range too large: {duration_days} days requested.")
                 return jsonify({"error": f"Date range cannot exceed 365 days. Requested: {duration_days} days."}), 400

            app.logger.info(f"Validated custom date range: {start_date_obj} to {end_date_obj} ({duration_days} days)")

            # Prepare for SQL query (inclusive start, exclusive end)
            # Add 1 day to end_date for '<' comparison to include the whole end day
            end_date_inclusive = end_date_obj + timedelta(days=1)

            where_clause = "WHERE reading_time >= %s AND reading_time < %s"
            query_params = (start_date_obj, end_date_inclusive) # Use tuple for parameters

            # Adjust label format based on duration
            if duration_days >= 1: # If range is 1 day or more, show date
                label_format = '%Y-%m-%d %H:%M'
            else: # Single day selection (duration is 0)
                label_format = '%H:%M:%S'

        # --- Logic Branch: Relative Time Range ---
        elif time_range:
            app.logger.debug(f"Processing relative time range request: {time_range}")
            if time_range == 'hour':
                where_clause = "WHERE reading_time >= NOW() - INTERVAL 1 HOUR"
                label_format = '%H:%M:%S'
            elif time_range == '8hour':
                where_clause = "WHERE reading_time >= NOW() - INTERVAL 8 HOUR"
                label_format = '%H:%M:%S'
            elif time_range == 'day':
                where_clause = "WHERE DATE(reading_time) = CURDATE()"
                label_format = '%H:%M'
            elif time_range == 'week':
                where_clause = "WHERE YEARWEEK(reading_time, 1) = YEARWEEK(CURDATE(), 1)"
                label_format = '%m-%d %H:%M'
            elif time_range == 'month':
                 where_clause = "WHERE YEAR(reading_time) = YEAR(CURDATE()) AND MONTH(reading_time) = MONTH(CURDATE())"
                 label_format = '%m-%d %H:%M'
            else:
                app.logger.warning(f"Invalid relative time range '{time_range}' received, defaulting to 'hour'.")
                where_clause = "WHERE reading_time >= NOW() - INTERVAL 1 HOUR"
                label_format = '%H:%M:%S'
                time_range = 'hour' # Correct the effective range for logging
        # --- Logic Branch: No valid parameters ---
        else:
             app.logger.error("No valid range or date parameters provided.")
             return jsonify({"error": "Missing time range or date parameters."}), 400

        # --- Construct and Execute Final Query ---
        # Add aggregation to lessen the load when dealing with large data sets
        final_query = query_base + where_clause + query_order # + query_limit
        app.logger.debug(f"Executing SQL: {final_query} with params: {query_params}")

        cursor = conn.cursor(dictionary=True)
        cursor.execute(final_query, query_params) # Pass params
        rows = cursor.fetchall()

        # --- Process Results ---
        for row in rows:
            if isinstance(row.get('reading_time'), datetime):
                 time_label = row['reading_time'].strftime(label_format)
                 chart_data["labels"].append(time_label)
            else:
                 chart_data["labels"].append(None) # Should not happen if reading_time is NOT NULL

            # Append sensor data (handle potential NULLs from DB)
            chart_data["temperatures"].append(row.get('temperature'))
            chart_data["humidities"].append(row.get('humidity'))

        log_range = f"{start_date_str} to {end_date_str}" if (start_date_str and end_date_str) else time_range
        app.logger.info(f"Fetched {len(rows)} raw data points for range '{log_range}'.")

    except Error as e:
        app.logger.error(f"Database error fetching chart data: {e}")
        return jsonify({"error": "Failed to fetch chart data due to database error"}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error processing chart data request: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

    return jsonify(chart_data)


# --- Run the App ---
if __name__ == '__main__':
    # When running with Gunicorn, this block is not executed
    # Gunicorn handles host and port binding via command line args
    # For direct development (python app.py)
    app.run(debug=True, host='0.0.0.0', port=5000)
