import os
import logging
from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta
from database import Database, SPH_VALUES, CYL_VALUES
from simulator import Simulator
from ai_helper import AIHelper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
db = Database()

# Initialize Rate Limiter to prevent bot spam and protect Gemini API costs
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],  # No default limits to ensure normal GET/navigational requests are not throttled
    storage_uri="memory://"
)

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": f"Rate limit exceeded. Please wait a moment before trying again."
    }), 429


# Frame database
FRAMES_CATALOG = {
    "Emerald-5": {"model": "Emerald-5", "price": 180.00, "style": "Round Gold/Green"},
    "Astro-3": {"model": "Astro-3", "price": 245.00, "style": "Square Dark Navy"},
    "Maestro-1": {"model": "Maestro-1", "price": 185.00, "style": "Square Matte Black"},
    "Vassili-1": {"model": "Vassili-1", "price": 178.00, "style": "Geometric Gold"}
}

def recalculate_all_predictions():
    """
    Recalculate predictions for all active orders using the AI engine.
    """
    orders = db.get_orders()
    state = db.get_system_state()
    for order in orders:
        if order["stage"] != "Delivered":
            prediction = AIHelper.get_vertex_prediction(order, state)
            order["tat_prediction"] = prediction
    db.save_orders(orders)

def trigger_background_prediction(order_id, state):
    import threading
    def update_single_prediction_bg(ord_id, sys_state):
        try:
            o = db.get_order(ord_id)
            if o:
                pred = AIHelper.get_vertex_prediction(o, sys_state)
                o["tat_prediction"] = pred
                ords = db.get_orders()
                for i, item in enumerate(ords):
                    if item["order_id"] == ord_id:
                        ords[i] = o
                        break
                db.save_orders(ords)
        except Exception as ex:
            logger.error(f"Error in background prediction update: {ex}")
            
    threading.Thread(target=update_single_prediction_bg, args=(order_id, state)).start()


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/orders", methods=["GET"])
def get_orders():
    orders = db.get_orders()
    
    # Filter parameters
    status = request.args.get("status")
    lens_type = request.args.get("lens_type")
    store_location = request.args.get("store_location")
    
    filtered_orders = []
    for o in orders:
        if status and o["stage"] != status:
            continue
        if lens_type and o["lens_type"] != lens_type:
            continue
        if store_location and o["source"] != store_location:
            continue
        filtered_orders.append(o)
        
    # Sort orders: Active first (placed date descending), then Delivered
    def sort_key(ord):
        is_active = 1 if ord["stage"] != "Delivered" else 0
        return (is_active, ord["placed_at"])
        
    filtered_orders.sort(key=sort_key, reverse=True)
    return jsonify(filtered_orders)

@app.route("/api/orders/<order_id>", methods=["GET"])
def get_order(order_id):
    order = db.get_order(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(order)

@app.route("/api/orders", methods=["POST"])
@limiter.limit("10 per minute")
def create_order():
    data = request.json
    if not data:
        return jsonify({"error": "Missing order payload"}), 400
        
    # Required fields
    required = ["customer_name", "source", "lens_type", "sph_od", "cyl_od", "axis_od", "sph_os", "cyl_os", "axis_os", "lens_index", "coating", "frame_model"]
    for r in required:
        if r not in data:
            return jsonify({"error": f"Missing required parameter: {r}"}), 400
            
    # Format prescription
    prescription = {
        "sph_od": float(data["sph_od"]),
        "cyl_od": float(data["cyl_od"]),
        "axis_od": int(data["axis_od"]),
        "sph_os": float(data["sph_os"]),
        "cyl_os": float(data["cyl_os"]),
        "axis_os": int(data["axis_os"])
    }
    
    # Look up frame
    frame = FRAMES_CATALOG.get(data["frame_model"], {"model": data["frame_model"], "price": 120.00, "style": "Standard Acetate"})
    
    # Determine SLA
    lens_type = data["lens_type"]
    sla_hours = 48 if lens_type == "Single Vision" else (96 if lens_type == "Bifocal" else 120)
    
    # Check inventory rules
    is_in_house = db._check_prescription_in_house(prescription, float(data["lens_index"]), data["coating"])
    
    # Double check actual stock if in-house
    sourcing_status = "Out-of-House (Sourced)"
    stock_allocated = False
    
    if is_in_house:
        stock_allocated, sourcing_status = db.allocate_stock(prescription)
            
    # Placement time is current simulated system time
    sys_state = db.get_system_state()
    placed_at = sys_state["system_time"]
    
    # SLA due time
    due_at = (datetime.fromisoformat(placed_at) + timedelta(hours=sla_hours)).isoformat()
    
    orders = db.get_orders()
    order_id = f"DH-{1000 + len(orders)}"
    
    new_order = {
        "order_id": order_id,
        "customer_name": data["customer_name"],
        "source": data["source"],
        "lens_type": lens_type,
        "prescription": prescription,
        "lens_index": float(data["lens_index"]),
        "coating": data["coating"],
        "frame": frame,
        "placed_at": placed_at,
        "sla_hours": sla_hours,
        "sla_due_at": due_at,
        "stage": "Order Placed",
        "sourcing_status": sourcing_status,
        "qc_fail_count": 0,
        "history": [
            {"stage": "Order Placed", "timestamp": placed_at, "reason": "Order registered into Daniel Hunter system."}
        ],
        "delay_reason": "",
        "tat_prediction": None
    }
    # Set initial prediction instantly
    initial_pred = AIHelper.get_instant_heuristic_prediction(new_order, sys_state)
    new_order["tat_prediction"] = initial_pred
    
    orders.append(new_order)
    db.save_orders(orders)
    
    # Run the prediction update in a background thread so the HTTP request completes instantly
    trigger_background_prediction(order_id, sys_state)
    
    logger.info(f"Created new order {order_id} with Sourcing Status: {sourcing_status}")
    
    return jsonify(new_order), 201

@app.route("/api/orders/<order_id>/status", methods=["POST"])
@limiter.limit("15 per minute")
def update_order_status(order_id):
    data = request.json
    if not data or "stage" not in data:
        return jsonify({"error": "Missing 'stage' parameter"}), 400
        
    order = db.get_order(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
        
    new_stage = data["stage"]
    delay_reason = data.get("delay_reason", "")
    
    # Logic for QC Rejection (loop back to Lab Processing)
    qc_failed = False
    if new_stage == "QC Failed":
        qc_failed = True
        new_stage = "Lab Processing" # Loops back
        order["qc_fail_count"] = order.get("qc_fail_count", 0) + 1
        
    sys_state = db.get_system_state()
    current_time = sys_state["system_time"]
    
    # Append history
    reason_text = delay_reason
    if qc_failed:
        reason_text = f"QC Failed: {delay_reason or 'Lenses rejected at inspection. Recutting initiated.'}"
        
    order["history"].append({
        "stage": new_stage if not qc_failed else "QC Failed (Lab Loop)",
        "timestamp": current_time,
        "reason": reason_text
    })
    
    order["stage"] = new_stage
    order["delay_reason"] = reason_text
    
    # Calculate instant heuristic prediction to return immediately and update the database
    initial_prediction = AIHelper.get_instant_heuristic_prediction(order, sys_state)
    order["tat_prediction"] = initial_prediction
    
    # Re-evaluate predictions immediately
    orders = db.get_orders()
    for idx, o in enumerate(orders):
        if o["order_id"] == order_id:
            orders[idx] = order
            break
            
    db.save_orders(orders)
    
    # Run the prediction update in a background thread so the HTTP request completes instantly
    trigger_background_prediction(order_id, sys_state)
    
    return jsonify(order)

@app.route("/api/inventory", methods=["GET"])
def get_inventory():
    return jsonify(db.get_inventory())

@app.route("/api/inventory/stock", methods=["POST"])
@limiter.limit("30 per minute")
def update_stock():
    data = request.json
    if not data or "key" not in data or "quantity" not in data:
        return jsonify({"error": "Missing key or quantity"}), 400
        
    db.update_inventory_stock(data["key"], int(data["quantity"]))
    return jsonify({"success": True})

@app.route("/api/simulator", methods=["GET"])
def get_simulator():
    return jsonify(db.get_system_state())

@app.route("/api/simulator/tick", methods=["POST"])
@limiter.limit("30 per minute")
def tick_simulator():
    data = request.json
    hours = int(data.get("hours", 1))
    
    # Advance system clock, update predictions, log alerts
    new_alerts = Simulator.advance_time(db, hours)
    
    # Run predictions update in a background thread
    import threading
    threading.Thread(target=recalculate_all_predictions).start()
    
    return jsonify({
        "system_state": db.get_system_state(),
        "alerts_triggered": new_alerts
    })

@app.route("/api/simulator/bottleneck", methods=["POST"])
@limiter.limit("30 per minute")
def set_bottleneck():
    data = request.json
    if not data:
        return jsonify({"error": "Missing configuration"}), 400
        
    state = db.get_system_state()
    
    if "is_bottleneck_lab" in data:
        state["is_bottleneck_lab"] = bool(data["is_bottleneck_lab"])
    if "is_bottleneck_coating" in data:
        state["is_bottleneck_coating"] = bool(data["is_bottleneck_coating"])
    if "is_bottleneck_sourcing" in data:
        state["is_bottleneck_sourcing"] = bool(data["is_bottleneck_sourcing"])
        
    db.save_system_state(state)
    
    # Run predictions update in a background thread
    import threading
    threading.Thread(target=recalculate_all_predictions).start()
    
    return jsonify(state)

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(db.get_alerts())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
