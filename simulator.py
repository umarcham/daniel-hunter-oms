from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Standard stage durations in hours
STAGE_BASE_HOURS = {
    "Order Placed": 0,
    "Lens Sourcing": {
        "In-House (Allocated)": 2,
        "Out-of-House (Sourced)": 48
    },
    "Lab Processing": {
        "Single Vision": 8,
        "Bifocal": 24,
        "Progressive": 36
    },
    "Coating": {
        "None": 0,
        "Anti-Reflective": 12,
        "Blue Cut": 24,
        "Photochromic": 24
    },
    "QC Check": 4,
    "Ready for Dispatch": 2,
    "Shipped": 24,
    "Delivered": 0
}

class Simulator:
    @staticmethod
    def calculate_stage_duration(stage, order, system_state):
        """
        Calculate standard duration of a stage for a specific order in hours,
        considering bottlenecks and order properties.
        """
        if stage == "Order Placed":
            return STAGE_BASE_HOURS["Order Placed"]
            
        elif stage == "Lens Sourcing":
            status = order.get("sourcing_status", "In-House (Allocated)")
            base = STAGE_BASE_HOURS["Lens Sourcing"].get(status, 2)
            if system_state.get("is_bottleneck_sourcing", False) and status == "Out-of-House (Sourced)":
                return base * 2.0 # Sourcing bottleneck doubles vendor delivery time
            return base
            
        elif stage == "Lab Processing":
            lens_type = order.get("lens_type", "Single Vision")
            base = STAGE_BASE_HOURS["Lab Processing"].get(lens_type, 8)
            # Apply lab bottleneck if active
            if system_state.get("is_bottleneck_lab", False):
                base *= 2.0
            # If there are QC failures, add additional labor time per failure
            qc_fails = order.get("qc_fail_count", 0)
            if qc_fails > 0:
                base += (12 * qc_fails) # Add 12 hours of re-cutting work per failure
            return base
            
        elif stage == "Coating":
            coating = order.get("coating", "None")
            base = STAGE_BASE_HOURS["Coating"].get(coating, 0)
            if base > 0 and system_state.get("is_bottleneck_coating", False):
                base *= 2.0
            return base
            
        elif stage == "QC Check":
            base = STAGE_BASE_HOURS["QC Check"]
            return base
            
        elif stage == "Ready for Dispatch":
            return STAGE_BASE_HOURS["Ready for Dispatch"]
            
        elif stage == "Shipped":
            return STAGE_BASE_HOURS["Shipped"]
            
        return 0

    @staticmethod
    def get_remaining_stages(current_stage):
        stages = [
            "Order Placed",
            "Lens Sourcing",
            "Lab Processing",
            "Coating",
            "QC Check",
            "Ready for Dispatch",
            "Shipped",
            "Delivered"
        ]
        try:
            idx = stages.index(current_stage)
            return stages[idx:]
        except ValueError:
            return ["Delivered"]

    @classmethod
    def get_heuristic_remaining_hours(cls, order, system_state):
        """
        Calculate remaining hours using deterministic heuristics.
        Used as base data and fallback.
        """
        current_stage = order.get("stage", "Order Placed")
        if current_stage == "Delivered":
            return 0
            
        remaining_stages = cls.get_remaining_stages(current_stage)
        total_hours = 0
        
        # Calculate for each remaining stage
        for i, stage in enumerate(remaining_stages):
            duration = cls.calculate_stage_duration(stage, order, system_state)
            if i == 0:
                # For the current stage, we should ideally subtract time already spent.
                # However, for simple planning, we assume half of the stage duration is remaining
                # unless we have detailed transition history. Let's see if we can calculate it:
                placed_or_last_transition = order["placed_at"]
                history = order.get("history", [])
                if history:
                    placed_or_last_transition = history[-1]["timestamp"]
                
                try:
                    sys_time = datetime.fromisoformat(system_state["system_time"])
                    last_time = datetime.fromisoformat(placed_or_last_transition)
                    elapsed = (sys_time - last_time).total_seconds() / 3600.0
                    remaining = max(0, duration - elapsed)
                    total_hours += remaining
                except Exception:
                    total_hours += duration / 2.0
            else:
                total_hours += duration
                
        return round(total_hours, 1)

    @classmethod
    def advance_time(cls, db, hours):
        """
        Advance simulated clock and check for status progression/alerts.
        """
        state = db.get_system_state()
        current_time = datetime.fromisoformat(state["system_time"])
        new_time = current_time + timedelta(hours=hours)
        state["system_time"] = new_time.isoformat()
        db.save_system_state(state)
        
        orders = db.get_orders()
        alerts_created = []
        
        for order in orders:
            if order["stage"] == "Delivered":
                continue
                
            # Calculate time remaining on SLA
            due_at = datetime.fromisoformat(order["sla_due_at"])
            remaining_sla_hours = (due_at - new_time).total_seconds() / 3600.0
            
            # Predict completion time using AI fallback (or prompt in app)
            predicted_remaining = cls.get_heuristic_remaining_hours(order, state)
            
            # Check for direct breach (due time passed but not delivered)
            if remaining_sla_hours < 0:
                # SLA Breached
                message = f"🚨 SLA Breach Alert: Order {order['order_id']} for {order['customer_name']} has breached SLA. SLA expired {abs(remaining_sla_hours):.1f} hours ago."
                alert_type = "SLA BREACH"
            elif predicted_remaining > remaining_sla_hours:
                # Predicted to breach
                message = f"⚠️ Predictive Alert: Order {order['order_id']} ({order['customer_name']}) is likely to breach. SLA remaining: {remaining_sla_hours:.1f}h, Predicted time: {predicted_remaining:.1f}h."
                alert_type = "BREACH RISK"
            else:
                alert_type = None
                
            if alert_type:
                # Log mock WhatsApp and Email alerts if not already logged recently for this stage
                existing_alerts = db.get_alerts()
                already_alerted = False
                for alt in existing_alerts:
                    # Avoid spamming multiple identical alerts for same order, stage, and alert type
                    if alt["order_id"] == order["order_id"] and alert_type in alt["message"] and order["stage"] in alt["message"]:
                        already_alerted = True
                        break
                        
                if not already_alerted:
                    # Log Email mock
                    email_msg = f"[EMAIL TO TEAM] Subject: {alert_type} - {order['order_id']}\n{message}"
                    email_alert = db.log_alert(order["order_id"], "Email", email_msg)
                    alerts_created.append(email_alert)
                    
                    # Log WhatsApp mock
                    wa_msg = f"[WHATSAPP TO TEAM] {message}"
                    wa_alert = db.log_alert(order["order_id"], "WhatsApp", wa_msg)
                    alerts_created.append(wa_alert)
                    
        db.save_orders(orders)
        return alerts_created
