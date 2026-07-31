import unittest
import sys
import os

# Add path to the src folder
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../eq_src')))

from eq_data import JmaEqData, EqSource, EqType

class TestJmaEqData(unittest.TestCase):
    
    def test_voice_text_forecast(self):
        """Case 2: Test text generation for Forecast (Weak)"""
        data = JmaEqData(
            source=EqSource.WOLFX,
            type=EqType.FORECAST,
            event_id="test01",
            hypocenter_name="千葉県北西部",
            max_intensity="5-",
            predicted_home_scale=25 # Equivalent to Intensity 3
        )
        text = data.get_voice_text()
        self.assertIn("緊急地震速報", text) # Emergency Earthquake Prompt
        self.assertIn("予測震度、3", text)
        self.assertIn("最大震度5弱", text)

    def test_voice_text_warning(self):
        """Case 3: Test text generation for Warning (Strong) - checking repetitions"""
        data = JmaEqData(
            source=EqSource.WOLFX,
            type=EqType.WARNING,
            event_id="test02",
            hypocenter_name="東京湾",
            max_intensity="6-", # 6-Lower
            predicted_home_scale=49 # Equivalent to Intensity 5弱
        )
        text = data.get_voice_text()
        # Check if the phrase is repeated 3 times
        self.assertEqual(text.count("予測震度、5弱。"), 3)
        # Verify intensity conversion
        self.assertIn("最大震度は、6弱", text)

    def test_voice_text_nied(self):
        """Case 1: Test text generation for NIED early detection"""
        data = JmaEqData(
            source=EqSource.NIED,
            type=EqType.REALTIME,
            event_id="time01",
            hypocenter_name="南西", # Direction
            max_intensity="2"
        )
        text = data.get_voice_text()
        self.assertEqual(text, "南西方面で地震を検知しました。最大震度、2。")

if __name__ == "__main__":
    unittest.main()