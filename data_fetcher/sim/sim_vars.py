# data_fetcher/sim/sim_vars.py

STANDARD_SIM_VARS = {
    # Aircraft identity / debug
    "aircraft_title": {
        "simvar": "TITLE",
        "python_simconnect_name": "TITLE",
        "unit": "string",
    },

    # Position / altitude
    "indicated_altitude_ft": {
        "simvar": "INDICATED ALTITUDE",
        "python_simconnect_name": "INDICATED_ALTITUDE",
        "unit": "feet",
    },
    "true_altitude_ft": {
        "simvar": "PLANE ALTITUDE",
        "python_simconnect_name": "PLANE_ALTITUDE",
        "unit": "feet",
    },
    "pressure_altitude_m": {
        "simvar": "PRESSURE ALTITUDE",
        "python_simconnect_name": "PRESSURE_ALTITUDE",
        "unit": "meters",
    },
    "latitude_deg": {
        "simvar": "PLANE LATITUDE",
        "python_simconnect_name": "PLANE_LATITUDE",
        "unit": "degrees",
    },
    "longitude_deg": {
        "simvar": "PLANE LONGITUDE",
        "python_simconnect_name": "PLANE_LONGITUDE",
        "unit": "degrees",
    },

    # Speed
    "mach": {
        "simvar": "AIRSPEED MACH",
        "python_simconnect_name": "AIRSPEED_MACH",
        "unit": "mach",
    },
    "true_airspeed_kt": {
        "simvar": "AIRSPEED TRUE",
        "python_simconnect_name": "AIRSPEED_TRUE",
        "unit": "knots",
    },
    "ground_speed_kt": {
        "simvar": "GROUND VELOCITY",
        "python_simconnect_name": "GROUND_VELOCITY",
        "unit": "knots",
    },
    "vertical_speed_fps": {
        "simvar": "VERTICAL SPEED",
        "python_simconnect_name": "VERTICAL_SPEED",
        "unit": "feet per second",
    },

    # Weight / fuel
    "gross_weight_lb": {
        "simvar": "TOTAL WEIGHT",
        "python_simconnect_name": "TOTAL_WEIGHT",
        "unit": "pounds",
    },
    "engine_count": {
        "simvar": "NUMBER OF ENGINES",
        "python_simconnect_name": "NUMBER_OF_ENGINES",
        "unit": "number",
    },
    "engine_type": {
        "simvar": "ENGINE TYPE",
        "python_simconnect_name": "ENGINE_TYPE",
        "unit": "enum",
    },
    "fuel_remaining_lb": {
        "simvar": "FUEL TOTAL QUANTITY WEIGHT",
        "python_simconnect_name": "FUEL_TOTAL_QUANTITY_WEIGHT",
        "unit": "pounds",
    },
    "fuel_remaining_lb_ex1": {
        "simvar": "FUEL TOTAL QUANTITY WEIGHT EX1",
        "python_simconnect_name": "FUEL_TOTAL_QUANTITY_WEIGHT_EX1",
        "unit": "pounds",
    },
    "fuel_weight_per_gallon_lb": {
        "simvar": "FUEL WEIGHT PER GALLON",
        "python_simconnect_name": "FUEL_WEIGHT_PER_GALLON",
        "unit": "pounds",
    },
    "turb_eng_fuel_flow_pph": {
        "simvar": "TURB ENG FUEL FLOW PPH:index",
        "python_simconnect_name": "TURB_ENG_FUEL_FLOW_PPH:index",
        "unit": "pounds per hour",
    },
    "eng_fuel_flow_pph": {
        "simvar": "ENG FUEL FLOW PPH:index",
        "python_simconnect_name": "ENG_FUEL_FLOW_PPH:index",
        "unit": "pounds per hour",
    },
    "eng_fuel_flow_pph_ssl": {
        "simvar": "ENG FUEL FLOW PPH SSL:index",
        "python_simconnect_name": "ENG_FUEL_FLOW_PPH_SSL:index",
        "unit": "pounds per hour",
    },
    "recip_eng_fuel_flow_pph": {
        "simvar": "RECIP ENG FUEL FLOW:index",
        "python_simconnect_name": "RECIP_ENG_FUEL_FLOW:index",
        "unit": "pounds per hour",
    },
    "eng_fuel_flow_gph": {
        "simvar": "ENG FUEL FLOW GPH:index",
        "python_simconnect_name": "ENG_FUEL_FLOW_GPH:index",
        "unit": "gallons per hour",
    },
    "turb_eng_corrected_ff": {
        "simvar": "TURB ENG CORRECTED FF:index",
        "python_simconnect_name": "TURB_ENG_CORRECTED_FF:index",
        "unit": "pounds per hour",
    },
    "fuelsystem_line_fuel_flow": {
        "simvar": "FUELSYSTEM LINE FUEL FLOW:index",
        "python_simconnect_name": "FUELSYSTEM_LINE_FUEL_FLOW:index",
        "unit": "gallons per hour",
    },

    # Weather / atmosphere
    "ambient_temperature_c": {
        "simvar": "AMBIENT TEMPERATURE",
        "python_simconnect_name": "AMBIENT_TEMPERATURE",
        "unit": "celsius",
    },
    "ambient_wind_velocity_kt": {
        "simvar": "AMBIENT WIND VELOCITY",
        "python_simconnect_name": "AMBIENT_WIND_VELOCITY",
        "unit": "knots",
    },
    "ambient_wind_direction_deg": {
        "simvar": "AMBIENT WIND DIRECTION",
        "python_simconnect_name": "AMBIENT_WIND_DIRECTION",
        "unit": "degrees",
    },

    # Optional: aircraft-axis wind
    "aircraft_wind_x_kt": {
        "simvar": "AIRCRAFT WIND X",
        "python_simconnect_name": "AIRCRAFT_WIND_X",
        "unit": "knots",
    },
    "aircraft_wind_y_kt": {
        "simvar": "AIRCRAFT WIND Y",
        "python_simconnect_name": "AIRCRAFT_WIND_Y",
        "unit": "knots",
    },
    "aircraft_wind_z_kt": {
        "simvar": "AIRCRAFT WIND Z",
        "python_simconnect_name": "AIRCRAFT_WIND_Z",
        "unit": "knots",
    },

    # State
    "on_ground": {
        "simvar": "SIM ON GROUND",
        "python_simconnect_name": "SIM_ON_GROUND",
        "unit": "bool",
    },
}
