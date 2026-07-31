import logging
import logging.handlers
import os
import math
import csv
from typing import List, Dict, Tuple

# Import from eq_config.eq_const will be done later
# from eq_config.eq_const import HOME_LATITUDE, HOME_LONGITUDE 

class EqLogger:
    @staticmethod
    def setup_logger(name: str = "alert_jma", log_dir: str = "../eq_log") -> logging.Logger:
        """
        Create a logger with log rotation
        """
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO) # default
        
        # Log format
        formatter = logging.Formatter(
            '%(asctime)s - [%(levelname)s] - %(filename)s:%(lineno)d - %(message)s'
        )
        
        # Rotating file output (1MB x 5 generations)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "system.log"),
            maxBytes=1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        
        # Console output
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        
        return logger

class GeoUtils:
    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate the distance (km) between two points using the Haversine formula
        """
        R = 6371.0  # Earth radius in kilometers

        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        
        a = (math.sin(d_lat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(d_lon / 2) ** 2)
        
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    @staticmethod
    def load_nied_stations(csv_path: str) -> List[Dict]:
        """
        en: Load NIED station list from CSV
        """
        stations = []
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Assuming CSV headers are 'code', 'name', 'lat', 'lon'
                    stations.append({
                        "code": row['code'],
                        "name": row['name'],
                        "lat": float(row['lat']),
                        "lon": float(row['lon'])
                    })
        except Exception as e:
            # Instead of printing here, let the caller log the error
            raise e
        return stations