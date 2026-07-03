import os
import json
import logging
import requests
import time
from datetime import datetime
from simulator import Simulator
from dotenv import load_dotenv

load_dotenv()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AIHelper:
    _last_429_time = 0.0

    @staticmethod
    def get_vertex_prediction(order, system_state):
        """
        Queries Gemini 2.5 Flash on Vertex AI to predict remaining TAT and SLA breach risk.
        Falls back to heuristic prediction if credentials fail or are missing.
        """
        # Check cache if prediction is already computed and can be reused
        cached = order.get("tat_prediction")
        if cached and isinstance(cached, dict) and cached.get("source") in ["GCP Vertex AI", "Local Predictive Heuristics (API Fallback)"]:
            cached_inputs = cached.get("inputs", {})
            current_inputs = {
                "stage": order.get("stage"),
                "qc_fail_count": order.get("qc_fail_count", 0),
                "delay_reason": order.get("delay_reason", ""),
                "is_bottleneck_lab": system_state.get("is_bottleneck_lab", False),
                "is_bottleneck_coating": system_state.get("is_bottleneck_coating", False),
                "is_bottleneck_sourcing": system_state.get("is_bottleneck_sourcing", False)
            }
            if cached_inputs == current_inputs:
                try:
                    cached_at = datetime.fromisoformat(cached.get("cached_at"))
                    sys_time = datetime.fromisoformat(system_state["system_time"])
                    elapsed_hours = max(0.0, (sys_time - cached_at).total_seconds() / 3600.0)
                except Exception:
                    elapsed_hours = 0.0
                
                pred_remaining = max(0.1, cached["predicted_remaining_hours"] - elapsed_hours)
                
                # Calculate remaining SLA hours
                try:
                    due_at = datetime.fromisoformat(order["sla_due_at"])
                    sys_time = datetime.fromisoformat(system_state["system_time"])
                    sla_remaining_hours = round((due_at - sys_time).total_seconds() / 3600.0, 1)
                except Exception:
                    sla_remaining_hours = order.get("sla_hours", 48)
                
                if sla_remaining_hours <= 0:
                    breach_prob = 100.0
                elif pred_remaining > sla_remaining_hours:
                    excess = pred_remaining - sla_remaining_hours
                    breach_prob = min(99.0, 50.0 + (excess / max(1, sla_remaining_hours)) * 50.0)
                else:
                    ratio = pred_remaining / max(1, sla_remaining_hours)
                    breach_prob = max(0.0, round(ratio * 75.0, 1))
                
                return {
                    "predicted_remaining_hours": round(pred_remaining, 1),
                    "breach_probability": round(breach_prob, 1),
                    "reasoning": cached.get("reasoning"),
                    "source": cached.get("source"),
                    "cached_at": cached.get("cached_at"),
                    "inputs": cached_inputs
                }

        creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GOOGLE_APPLICATION_CREDENTIALS.json")
        
        # Calculate base heuristic remaining hours to feed as baseline context to the LLM
        heuristic_hours = Simulator.get_heuristic_remaining_hours(order, system_state)
        
        # Calculate remaining SLA hours
        try:
            due_at = datetime.fromisoformat(order["sla_due_at"])
            sys_time = datetime.fromisoformat(system_state["system_time"])
            sla_remaining_hours = round((due_at - sys_time).total_seconds() / 3600.0, 1)
        except Exception:
            sla_remaining_hours = order.get("sla_hours", 48)

        # Context details for prompt
        order_context = {
            "order_id": order.get("order_id"),
            "customer": order.get("customer_name"),
            "lens_type": order.get("lens_type"),
            "prescription": order.get("prescription"),
            "lens_index": order.get("lens_index"),
            "coating": order.get("coating"),
            "frame": order.get("frame", {}).get("model", "Unknown"),
            "sourcing_status": order.get("sourcing_status"),
            "current_stage": order.get("stage"),
            "qc_fail_count": order.get("qc_fail_count", 0),
            "delay_reason": order.get("delay_reason", ""),
            "active_bottlenecks": {
                "lab_processing": system_state.get("is_bottleneck_lab", False),
                "coating": system_state.get("is_bottleneck_coating", False),
                "sourcing": system_state.get("is_bottleneck_sourcing", False)
            },
            "sla_total_hours": order.get("sla_hours", 48),
            "sla_remaining_hours": sla_remaining_hours,
            "heuristic_baseline_hours": heuristic_hours
        }

        prompt = f"""
Analyze this eyewear order and system state for Daniel Hunter Eyewear:
{json.dumps(order_context, indent=2)}

Predict:
1. 'predicted_remaining_hours' (float): The total remaining hours to complete the order lifecycle. Note that out-of-house sourcing, lens indices (e.g., 1.67, 1.74), premium coatings, active bottlenecks, and past QC failures increase this.
2. 'breach_probability' (float): A percentage from 0 to 100 of how likely this order is to breach its SLA, compared with the 'sla_remaining_hours'.
3. 'reasoning' (string): A short, elegant, luxury-themed bulleted list detailing the reasoning behind your prediction (e.g., "• Sourced progressive lens requires 48h external shipping. • Lab bottleneck is adding 1.5x delay.").

Output MUST be valid JSON matching this schema:
{{
  "predicted_remaining_hours": 32.5,
  "breach_probability": 65.0,
  "reasoning": "• Out-of-house progressive lens requires 48 hours to source.\\n• Active coating bottleneck adds an estimated 8 hours process delay.\\n• Minimal buffer remaining against the 120-hour SLA."
}}
"""

        # Check if the API is in cooldown due to a recent 429 rate limit error
        cooldown_duration = 60.0  # seconds
        api_in_cooldown = (time.time() - AIHelper._last_429_time) < cooldown_duration

        api_key = os.environ.get("GEMINI_API_KEY")

        # Try Developer API query if key is present
        if api_key and not api_in_cooldown:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
                headers = {
                    "Content-Type": "application/json"
                }
                payload = {
                    "contents": [{
                        "role": "user",
                        "parts": [{"text": prompt}]
                    }],
                    "systemInstruction": {
                        "parts": [{"text": "You are a senior Operations AI engine for a luxury eyewear brand. Provide highly accurate, JSON-formatted predictions."}]
                    },
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.2
                    }
                }
                res = requests.post(url, headers=headers, json=payload, timeout=15)
                if res.status_code == 200:
                    logger.info("Successfully completed Gemini 2.5 Flash query via Developer API key")
                    res_json = res.json()
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        if text_content:
                            parsed = json.loads(text_content.strip())
                            if "predicted_remaining_hours" in parsed and "breach_probability" in parsed:
                                return {
                                    "predicted_remaining_hours": float(parsed["predicted_remaining_hours"]),
                                    "breach_probability": float(parsed["breach_probability"]),
                                    "reasoning": parsed.get("reasoning", "No detailed reasoning provided."),
                                    "source": "GCP Vertex AI",
                                    "cached_at": system_state.get("system_time"),
                                    "inputs": {
                                        "stage": order.get("stage"),
                                        "qc_fail_count": order.get("qc_fail_count", 0),
                                        "delay_reason": order.get("delay_reason", ""),
                                        "is_bottleneck_lab": system_state.get("is_bottleneck_lab", False),
                                        "is_bottleneck_coating": system_state.get("is_bottleneck_coating", False),
                                        "is_bottleneck_sourcing": system_state.get("is_bottleneck_sourcing", False)
                                    }
                                }
                else:
                    if res.status_code == 429:
                        AIHelper._last_429_time = time.time()
                        logger.warning("Gemini Developer API rate limit (429) hit. Entering 60-second API cooldown. Falling back to local heuristic prediction.")
                    else:
                        logger.error(f"Gemini Developer API returned status {res.status_code}: {res.text}. Falling back to heuristic.")
            except Exception as e:
                logger.warning(f"Failed to query Gemini Developer API directly: {e}")

        # Try Vertex AI API query if no key is present (fallback connection)
        elif os.path.exists(creds_path) and not api_in_cooldown:
            try:
                import google.auth
                from google.auth.transport.requests import Request
                
                # Load credentials and project ID
                credentials, project_id = google.auth.load_credentials_from_file(
                    creds_path,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                credentials.refresh(Request())
                access_token = credentials.token
                
                # Fallback to GCP_PROJECT_ID env var if not returned from credentials
                if not project_id:
                    project_id = os.getenv("GCP_PROJECT_ID", "")
                
                region = "us-central1"
                vertex_url = f"https://{region}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{region}/publishers/google/models/gemini-2.5-flash:generateContent"
                
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "contents": [{
                        "role": "user",
                        "parts": [{"text": prompt}]
                    }],
                    "systemInstruction": {
                        "parts": [{"text": "You are a senior Operations AI engine for a luxury eyewear brand. Provide highly accurate, JSON-formatted predictions."}]
                    },
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.2
                    }
                }
                
                res = requests.post(vertex_url, headers=headers, json=payload, timeout=15)
                if res.status_code == 200:
                    logger.info("Successfully completed Gemini 2.5 Flash query via Vertex AI")
                    res_json = res.json()
                    
                    # Extract response text
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        if text_content:
                            parsed = json.loads(text_content.strip())
                            # Validate fields
                            if "predicted_remaining_hours" in parsed and "breach_probability" in parsed:
                                return {
                                    "predicted_remaining_hours": float(parsed["predicted_remaining_hours"]),
                                    "breach_probability": float(parsed["breach_probability"]),
                                    "reasoning": parsed.get("reasoning", "No detailed reasoning provided."),
                                    "source": "GCP Vertex AI",
                                    "cached_at": system_state.get("system_time"),
                                    "inputs": {
                                        "stage": order.get("stage"),
                                        "qc_fail_count": order.get("qc_fail_count", 0),
                                        "delay_reason": order.get("delay_reason", ""),
                                        "is_bottleneck_lab": system_state.get("is_bottleneck_lab", False),
                                        "is_bottleneck_coating": system_state.get("is_bottleneck_coating", False),
                                        "is_bottleneck_sourcing": system_state.get("is_bottleneck_sourcing", False)
                                    }
                                }
                else:
                    if res.status_code == 429:
                        AIHelper._last_429_time = time.time()
                        logger.warning("Vertex AI API rate limit (429) hit. Entering 60-second API cooldown. Falling back to local heuristic prediction.")
                    else:
                        logger.error(f"Vertex AI API returned status {res.status_code}: {res.text}. Falling back to heuristic.")
            except Exception as ve:
                logger.warning(f"Failed to query Vertex AI, falling back. Error: {ve}")
        else:
            if api_in_cooldown:
                logger.info("Vertex AI API is in cooldown after rate limit. Bypassing API call and using local fallback.")
            else:
                logger.info("GOOGLE_APPLICATION_CREDENTIALS.json and GEMINI_API_KEY not found. Operating in fallback mode.")
            
        # Local heuristic fallback

        return AIHelper.get_fallback_prediction(order, system_state, heuristic_hours, sla_remaining_hours)

    @staticmethod
    def get_fallback_prediction(order, system_state, heuristic_hours, sla_remaining):
        """
        Local rule-based generator for predictions if Vertex AI is unavailable.
        Generates realistic data patterns with elegant reasoning.
        """
        # Determine breach probability based on predicted vs remaining SLA hours
        if sla_remaining <= 0:
            breach_prob = 100.0
        elif heuristic_hours > sla_remaining:
            # Over SLA
            excess = heuristic_hours - sla_remaining
            breach_prob = min(99.0, 50.0 + (excess / max(1, sla_remaining)) * 50.0)
        else:
            # Under SLA
            buffer = sla_remaining - heuristic_hours
            ratio = heuristic_hours / max(1, sla_remaining)
            breach_prob = max(0.0, round(ratio * 75.0, 1))

        # Generate custom bulleted reasons based on conditions
        bullets = []
        
        # Sourcing
        sourcing = order.get("sourcing_status", "In-House (Allocated)")
        if sourcing == "Out-of-House (Sourced)":
            bullets.append("• Special lens power or premium coating index requires external sourcing (adds 48 hours).")
            if system_state.get("is_bottleneck_sourcing", False):
                bullets.append("• Active courier backlog is delaying out-of-house vendor fulfillment.")
        else:
            bullets.append("• Prescription and lens options are in-house (allocated instantly).")

        # Lab Processing
        if order.get("stage") in ["Order Placed", "Lens Sourcing", "Lab Processing"]:
            if system_state.get("is_bottleneck_lab", False):
                bullets.append("• Processing lab is currently backed up, adding a 2.0x delay multiplier to lens edging.")
            if order.get("qc_fail_count", 0) > 0:
                bullets.append(f"• Experienced {order['qc_fail_count']} QC rejection(s), forcing re-cutting (+12 hours).")

        # Coating
        if order.get("coating", "None") != "None":
            if system_state.get("is_bottleneck_coating", False):
                bullets.append("• Premium coating oven is running at capacity, doubling coating wait time.")

        # Combined summary
        if not bullets:
            bullets.append("• Order is in final stages of fulfillment with healthy schedule margin.")
            
        reasoning = "\n".join(bullets)
        
        return {
            "predicted_remaining_hours": float(heuristic_hours),
            "breach_probability": round(breach_prob, 1),
            "reasoning": reasoning,
            "source": "Local Predictive Heuristics (API Fallback)"
        }

    @staticmethod
    def get_instant_heuristic_prediction(order, system_state):
        """
        Generate a fast, local heuristic prediction to update the UI instantly
        before refining via Vertex AI in the background.
        """
        heuristic_hours = Simulator.get_heuristic_remaining_hours(order, system_state)
        try:
            from datetime import datetime
            due_at = datetime.fromisoformat(order["sla_due_at"])
            sys_time = datetime.fromisoformat(system_state["system_time"])
            sla_remaining_hours = round((due_at - sys_time).total_seconds() / 3600.0, 1)
        except Exception:
            sla_remaining_hours = order.get("sla_hours", 48)

        pred = AIHelper.get_fallback_prediction(order, system_state, heuristic_hours, sla_remaining_hours)
        pred["source"] = "Local Predictive Heuristics (Initial)"
        return pred
