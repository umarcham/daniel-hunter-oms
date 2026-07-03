import unittest
import json
import os
from database import Database, DEFAULT_IN_HOUSE_RANGES
from simulator import Simulator
from app import app

class TestOMS(unittest.TestCase):
    def setUp(self):
        # Configure app for testing
        app.config["TESTING"] = True
        self.client = app.test_client()
        
        # Instantiate a clean database helper (resets database)
        self.db = Database()
        self.db.reset_db()
        
        # Globally mock requests.post to avoid hitting actual external APIs
        from unittest.mock import patch
        self.patcher = patch("requests.post")
        self.mock_post = self.patcher.start()
        
        # Configure default response for Vertex AI
        mock_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "predicted_remaining_hours": 12.0,
                            "breach_probability": 10.0,
                            "reasoning": "• Mocked test response"
                        })
                    }]
                }
            }]
        }
        self.mock_post.return_value.status_code = 200
        self.mock_post.return_value.json.return_value = mock_response

    def tearDown(self):
        self.patcher.stop()
        
    def test_database_initialization(self):
        """Verify the database seeds orders and state properly."""
        orders = self.db.get_orders()
        self.assertGreater(len(orders), 0)
        
        state = self.db.get_system_state()
        self.assertIn("system_time", state)
        self.assertFalse(state["is_bottleneck_lab"])

    def test_prescription_sourcing_rules(self):
        """Verify the boundary rules for in-house vs out-of-house lenses."""
        # Clean case: SPH -2.00, CYL -0.50, Index 1.56, Coating None (Should be In-House)
        rx_in = {"sph_od": -2.00, "cyl_od": -0.50, "sph_os": -1.00, "cyl_os": 0.00}
        self.assertTrue(self.db._check_prescription_in_house(rx_in, 1.56, "None"))
        
        # Bad SPH: -7.00 (exceeds -6.00 limit) -> Out-of-House
        rx_bad_sph = {"sph_od": -7.00, "cyl_od": -0.50, "sph_os": -1.00, "cyl_os": 0.00}
        self.assertFalse(self.db._check_prescription_in_house(rx_bad_sph, 1.56, "None"))
        
        # Bad CYL: -2.50 (exceeds -2.00 limit) -> Out-of-House
        rx_bad_cyl = {"sph_od": -2.00, "cyl_od": -2.50, "sph_os": -1.00, "cyl_os": 0.00}
        self.assertFalse(self.db._check_prescription_in_house(rx_bad_cyl, 1.56, "None"))
        
        # Bad Index: 1.67 -> Out-of-House
        self.assertFalse(self.db._check_prescription_in_house(rx_in, 1.67, "None"))
        
        # Bad Coating: Blue Cut -> Out-of-House
        self.assertFalse(self.db._check_prescription_in_house(rx_in, 1.56, "Blue Cut"))

    def test_simulator_durations(self):
        """Test that stage durations are computed correctly based on bottlenecks."""
        order = {
            "lens_type": "Single Vision",
            "sourcing_status": "In-House (Allocated)",
            "coating": "None",
            "qc_fail_count": 0
        }
        
        state_no_bottlenecks = {
            "is_bottleneck_lab": False,
            "is_bottleneck_coating": False,
            "is_bottleneck_sourcing": False
        }
        
        # Single vision baseline lab is 8 hours
        duration = Simulator.calculate_stage_duration("Lab Processing", order, state_no_bottlenecks)
        self.assertEqual(duration, 8.0)
        
        # Lab bottleneck active -> 8 * 2 = 16 hours
        state_bottleneck = state_no_bottlenecks.copy()
        state_bottleneck["is_bottleneck_lab"] = True
        duration_b = Simulator.calculate_stage_duration("Lab Processing", order, state_bottleneck)
        self.assertEqual(duration_b, 16.0)
        
        # QC Failed count > 0 should add re-edging penalty hours
        order_failed = order.copy()
        order_failed["qc_fail_count"] = 1
        duration_f = Simulator.calculate_stage_duration("Lab Processing", order_failed, state_no_bottlenecks)
        self.assertEqual(duration_f, 20.0) # 8 base + 12 penalty

    def test_api_endpoints(self):
        """Test Flask GET/POST operational API endpoints."""
        # 1. GET Orders
        res = self.client.get("/api/orders")
        self.assertEqual(res.status_code, 200)
        orders = json.loads(res.data)
        self.assertIsInstance(orders, list)
        
        # 2. POST Order (create new)
        new_order_payload = {
            "customer_name": "Tony Stark",
            "source": "Colaba Store",
            "lens_type": "Single Vision",
            "lens_index": "1.56",
            "coating": "None",
            "frame_model": "Emerald-5",
            "sph_od": -1.50,
            "cyl_od": -0.25,
            "axis_od": 90,
            "sph_os": -1.25,
            "cyl_os": 0.00,
            "axis_os": 0
        }
        res_create = self.client.post(
            "/api/orders",
            data=json.dumps(new_order_payload),
            content_type="application/json"
        )
        self.assertEqual(res_create.status_code, 201)
        data_create = json.loads(res_create.data)
        self.assertIn("order_id", data_create)
        self.assertEqual(data_create["sourcing_status"], "In-House (Allocated)")
        self.assertIsNotNone(data_create.get("tat_prediction"))
        self.assertEqual(data_create["tat_prediction"]["source"], "Local Predictive Heuristics (Initial)")
        
        # 3. POST Order Status (update stage to Lab Processing)
        order_id = data_create["order_id"]
        res_status = self.client.post(
            f"/api/orders/{order_id}/status",
            data=json.dumps({"stage": "Lab Processing", "delay_reason": "Moving into lab"}),
            content_type="application/json"
        )
        self.assertEqual(res_status.status_code, 200)
        data_status = json.loads(res_status.data)
        self.assertEqual(data_status["stage"], "Lab Processing")
        self.assertEqual(len(data_status["history"]), 2) # Placed + Lab Processing
        self.assertIsNotNone(data_status.get("tat_prediction"))
        self.assertEqual(data_status["tat_prediction"]["source"], "Local Predictive Heuristics (Initial)")
        
        # 4. POST QC Fail (loop back logic check)
        res_fail = self.client.post(
            f"/api/orders/{order_id}/status",
            data=json.dumps({"stage": "QC Failed", "delay_reason": "Air bubble in right lens"}),
            content_type="application/json"
        )
        self.assertEqual(res_fail.status_code, 200)
        data_fail = json.loads(res_fail.data)
        self.assertEqual(data_fail["stage"], "Lab Processing") # Looped back
        self.assertEqual(data_fail["qc_fail_count"], 1)
        self.assertIsNotNone(data_fail.get("tat_prediction"))
        self.assertEqual(data_fail["tat_prediction"]["source"], "Local Predictive Heuristics (Initial)")

    def test_simulator_ticks(self):
        """Test time progression simulator tick API."""
        res = self.client.post(
            "/api/simulator/tick",
            data=json.dumps({"hours": 12}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("system_state", data)
        self.assertIn("alerts_triggered", data)
    def test_caching_prediction(self):
        """Test that caching prediction saves Vertex AI API calls when inputs do not change."""
        from unittest.mock import patch
        from ai_helper import AIHelper
        
        # Reset API cooldown state for test isolation
        AIHelper._last_429_time = 0.0
        
        order = {
            "order_id": "DH-TEST-CACHE",
            "customer_name": "Test Customer",
            "lens_type": "Single Vision",
            "prescription": {"sph_od": -2.0, "cyl_od": -0.5, "sph_os": -1.0, "cyl_os": 0.0},
            "lens_index": 1.56,
            "coating": "None",
            "frame": {"model": "Emerald-5"},
            "placed_at": "2026-06-16T12:00:00",
            "sla_hours": 48,
            "sla_due_at": "2026-06-18T12:00:00",
            "stage": "Lab Processing",
            "sourcing_status": "In-House (Allocated)",
            "qc_fail_count": 0,
            "delay_reason": "",
            "tat_prediction": None
        }
        
        system_state = {
            "system_time": "2026-06-16T12:00:00",
            "is_bottleneck_lab": False,
            "is_bottleneck_coating": False,
            "is_bottleneck_sourcing": False
        }
        
        # Mocking requests.post to simulate successful Vertex AI prediction
        mock_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "predicted_remaining_hours": 10.0,
                            "breach_probability": 15.0,
                            "reasoning": "• Standard processing times apply."
                        })
                    }]
                }
            }]
        }
        
        with patch("requests.post") as mock_post, patch("os.path.exists", return_value=True):
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = mock_response
            
            # First prediction (uncached)
            prediction1 = AIHelper.get_vertex_prediction(order, system_state)
            self.assertEqual(prediction1["source"], "GCP Vertex AI")
            self.assertEqual(prediction1["predicted_remaining_hours"], 10.0)
            self.assertEqual(mock_post.call_count, 1)
            
            # Save prediction to order
            order["tat_prediction"] = prediction1
            
            # Second prediction with SAME state but time ticked by 2 hours
            system_state_ticked = system_state.copy()
            system_state_ticked["system_time"] = "2026-06-16T14:00:00"
            
            prediction2 = AIHelper.get_vertex_prediction(order, system_state_ticked)
            self.assertEqual(prediction2["source"], "GCP Vertex AI")
            # The remaining hours should be adjusted down: 10.0 - 2.0 = 8.0
            self.assertEqual(prediction2["predicted_remaining_hours"], 8.0)
            # No new HTTP request should have been made!
            self.assertEqual(mock_post.call_count, 1)

if __name__ == "__main__":
    unittest.main()
