# /home/DanDev/terrarium_webapp/app.py
from flask import Flask, render_template, jsonify, request
import mysql.connector
from mysql.connector import Error
import os
from datetime import datetime, date, timedelta, time

app = Flask(__name__)

# --- Database Configuration ---
DB_HOST = 'localhost'
DB_USER = 'terrarium_user'
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
                row['reading_time'] = row['reading_time'].isoformat()
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
    """
    API endpoint to fetch data formatted for Chart.js.
    Handles relative ranges and custom date ranges.
    Identifies gaps in custom ranges for annotation.
    Does NOT yet perform aggregation (returns raw data within limits).
    """
    time_range = request.args.get('range')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    app.logger.info(f"Request received - Range: {time_range}, Start: {start_date_str}, End: {end_date_str}")

    conn = get_db_connection()
    if not conn:
         return jsonify({"error": "Database connection failed"}), 500

    cursor = None
    final_labels = []
    final_temps = []
    final_humids = []
    gaps_identified = [] # List to store gap info for annotations

    query_params = None
    where_clause = ""
    label_format = '%H:%M:%S'
    # Define interval for gap detection
    # Using a fixed value for now, consider making it dynamic based on range
    GAP_THRESHOLD_MINUTES = 15
    gap_threshold_delta = timedelta(minutes=GAP_THRESHOLD_MINUTES)

    try:
        # --- Custom Date Range Logic ---
        if start_date_str and end_date_str:
            app.logger.debug("Processing custom date range request.")
            try:
                start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                app.logger.warning(f"Invalid date format: Start='{start_date_str}', End='{end_date_str}'")
                return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

            if end_date_obj < start_date_obj:
                app.logger.warning(f"Invalid range: End '{end_date_str}' before start '{start_date_str}'")
                return jsonify({"error": "End date cannot be before start date."}), 400

            duration_days = (end_date_obj - start_date_obj).days
            if duration_days > 365:
                 app.logger.warning(f"Date range too large: {duration_days} days requested.")
                 return jsonify({"error": f"Date range cannot exceed 365 days. Requested: {duration_days} days."}), 400

            app.logger.info(f"Validated custom date range: {start_date_obj} to {end_date_obj}")

            end_date_inclusive = end_date_obj + timedelta(days=1)
            where_clause = "WHERE reading_time >= %s AND reading_time < %s"
            query_params = (start_date_obj, end_date_inclusive)

            # --- Fetch Actual Data (Raw for now) ---
            query = f"""
                SELECT reading_time, temperature, humidity
                FROM readings
                {where_clause}
                ORDER BY reading_time ASC
            """
            app.logger.debug(f"Executing SQL: {query} with params: {query_params}")
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, query_params)
            rows = cursor.fetchall()
            app.logger.info(f"Fetched {len(rows)} actual data points for custom range.")

            # --- Process Rows and Identify Gaps ---
            last_reading_time = None
            if rows:
                # Determine appropriate label format based on duration
                if duration_days >= 1:
                     label_format = '%Y-%m-%d %H:%M' # Show date if range > 1 day
                else:
                     label_format = '%H:%M:%S' # For single day

                # Process first row
                first_row = rows[0]
                if isinstance(first_row.get('reading_time'), datetime):
                    current_time = first_row['reading_time']
                    final_labels.append(current_time.strftime(label_format))
                    final_temps.append(first_row.get('temperature'))
                    final_humids.append(first_row.get('humidity'))
                    last_reading_time = current_time

                # Process remaining rows
                for i in range(1, len(rows)):
                    row = rows[i]
                    if isinstance(row.get('reading_time'), datetime):
                        current_time = row['reading_time']
                        # Check for gap
                        if last_reading_time and (current_time - last_reading_time) > gap_threshold_delta:
                            # Gap detected! Record boundaries for annotation
                            gap_start_label = last_reading_time.strftime(label_format)
                            gap_end_label = current_time.strftime(label_format)
                            # Record labels adjacent to the gap
                            label_before_gap = gap_start_label # Label of last point before gap
                            label_after_gap = gap_end_label   # Label of first point after gap
                            gaps_identified.append({
                                "start": gap_start_label, # Annotation start
                                "end": gap_end_label,     # Annotation end
                                "label_before": label_before_gap,
                                "label_after": label_after_gap
                            })
                            app.logger.debug(f"Gap detected between {gap_start_label} and {gap_end_label}")

                        # Add current data point regardless of gap
                        final_labels.append(current_time.strftime(label_format))
                        final_temps.append(row.get('temperature'))
                        final_humids.append(row.get('humidity'))
                        last_reading_time = current_time # Update last seen time

        # --- Relative Time Range Logic (No Gap Detection Needed Here) ---
        elif time_range:
            app.logger.debug(f"Processing relative time range request: {time_range}")
            query_base = "SELECT reading_time, temperature, humidity FROM readings "
            query_order = " ORDER BY reading_time ASC"

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
                app.logger.warning(f"Invalid relative range '{time_range}', defaulting to 'hour'.")
                where_clause = "WHERE reading_time >= NOW() - INTERVAL 1 HOUR"
                label_format = '%H:%M:%S'
                time_range = 'hour'

            # Fetch data for relative range
            final_query = query_base + where_clause + query_order
            app.logger.debug(f"Executing SQL: {final_query}")
            cursor = conn.cursor(dictionary=True)
            cursor.execute(final_query) # No paramaters needed for relative ranges here
            rows = cursor.fetchall()
            app.logger.info(f"Fetched {len(rows)} data points for relative range '{time_range}'.")

            # Process rows for relative range
            for row in rows:
                if isinstance(row.get('reading_time'), datetime):
                     final_labels.append(row['reading_time'].strftime(label_format))
                     final_temps.append(row.get('temperature'))
                     final_humids.append(row.get('humidity'))

        # --- No valid parameters ---
        else:
             app.logger.error("No valid range or date parameters provided.")
             return jsonify({"error": "Missing time range or date parameters."}), 400

        # --- Prepare final JSON response ---
        chart_data = {
            "labels": final_labels,
            "temperatures": final_temps,
            "humidities": final_humids,
            "gaps": gaps_identified # Include gap info if any
        }

    except Error as e:
        app.logger.error(f"Database error processing chart data: {e}")
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
    app.run(debug=True, host='0.0.0.0', port=5000)
