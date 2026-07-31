from dataclasses import dataclass
from enum import Enum
from typing import Optional

class EqSource(Enum):
    WOLFX = "WOLFX"
    NIED = "NIED"
    SIMULATION = "SIMULATION"  # for testing

class EqType(Enum):
    FORECAST = "FORECAST"      # Case 2: Forecast (Wolfx)
    WARNING = "WARNING"        # Case 3: Warning (Wolfx)
    CANCEL = "CANCEL"          # Case 4: Cancel (Wolfx)
    REALTIME = "REALTIME"      # Case 1: Real-time report (NIED)

@dataclass
class JmaEqData:
    """
    Unified earthquake data format used internally in the system
 """
    source: EqSource
    type: EqType
    event_id: str              # Unique identifier (for NIED, use timestamp etc.) 
    
    # Earthquake source information
    hypocenter_name: str       # Hypocenter name (or observation point for NIED)
    magnitude: Optional[float] = None
    depth: Optional[str] = None
    
    # Seismic intensity information
    max_intensity: str = "0"         # Maximum seismic intensity of the earthquake (e.g., "4", "5+")
    predicted_home_scale: int = 0    # Predicted home seismic intensity scale(e.g., 30=Seismic Intensity 3)
    
    # Additional information (for voice synthesis)
    is_final: bool = False           # is final report
    original_text: str = ""          # raw data for logging

    def get_voice_text(self) -> str:
        """
        Generate voice text according to the data type 
        """
        # Convert predicted home scale to string (e.g., 30 -> "3", 45 -> "5-")
        home_scale_str = self._convert_scale_to_str(self.predicted_home_scale)
        
        # Format the maximum seismic intensity for voice reading (e.g., "5-" -> "5jaku")
        max_scale_str = self._format_max_intensity(self.max_intensity)

        # --- Case 4: Cancel report ---
        if self.type == EqType.CANCEL:
            return "The earlier emergency earthquake warning has been canceled."

        # --- Case 1: NIED (Early Detection) ---
        elif self.type == EqType.REALTIME:
            # hypocenter_name contains observation point names such as "Chiba Tateyama"
            # max_intensity is assumed to contain the class derived from the measured seismic intensity
            return f"{self.hypocenter_name}方面で地震を検知しました。最大震度、{max_scale_str}。"
        
        # --- Case 3: EEW Warning (Serious) ---
        elif self.type == EqType.WARNING:
            # Repeat the "Predicted seismic intensity, 3." part three times
            repeat_phrase = f"予測震度、{home_scale_str}。"
            
            return (
                f"{repeat_phrase * 3}　"
                f"{self.hypocenter_name}で強い地震が発生しました。"
                f"最大震度は、{max_scale_str}。"
            )
        
        # --- Case 2: EEW Forecast (General) ---
        elif self.type == EqType.FORECAST:
            return (
                f"緊急地震速報。予測震度、{home_scale_str}。"
                f"{self.hypocenter_name}で最大震度{max_scale_str}の地震が発生しました。"
            )
        
        return ""
    
    def _convert_scale_to_str(self, scale_int: int) -> str:
        """
        Convert Wolfx/JMA seismic intensity code (int) to voice text string
        10->1, 20->2, 30->3, 40->4, 45->5jaku, 50->5kyou, 55->6jaku, 60->6kyou, 70->7
        """
        if scale_int < 10:
            return "0"
        if scale_int < 15:
            return "1"
        if scale_int < 25:
            return "2"
        if scale_int < 35:
            return "3"
        if scale_int < 45:
            return "4"
        if scale_int < 50:
            return "5弱"
        if scale_int < 55:
            return "5強"
        if scale_int < 60:
            return "6弱"
        if scale_int < 65:
            return "6強"
        return "7"

    def _format_max_intensity(self, intensity: str) -> str:
        """
        Convert seismic intensity notation like "5-" or "5+" from WebAPI etc. to voice-friendly format "5jaku" or "5kyou"
        """
        return intensity.replace("-", "弱").replace("+", "強")