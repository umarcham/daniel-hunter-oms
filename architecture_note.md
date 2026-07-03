# Architecture Note: AI-Powered Order Management System

This document outlines the system architecture, AI models, and APIs used in building the Daniel Hunter Eyewear Order Management System (OMS).

---

## 1. System Architecture

The application is structured as a lightweight, low-latency full-stack application designed for real-time shop-floor monitoring:
* **Frontend Dashboard**: A responsive Single-Page Application (SPA) built using vanilla HTML5, CSS3, and JavaScript. The styling mimics the premium luxury storefront of Daniel Hunter Eyewear (cream `#F9F6F0`, crimson burgundy, and matte black). It displays KPIs, an active orders workspace, a live alert log, and an interactive 2D lens power inventory grid.
* **Backend API (Flask)**: A Python 3 Flask server that manages routing, order state transitions, and inventory stock changes. It runs a background simulation clock that updates SLA calculations.
* **Database Layer**: A self-contained JSON database (`db.json`) handled by `database.py`. This provides thread-safe file serialization, facilitating rapid, zero-setup local deployment.

---

## 2. AI Model Selection & Vertex AI API

For predicting order Turnaround Time (TAT) and SLA breach risks, we integrated **Google Gemini 2.5 Flash** queried via the **GCP Vertex AI API**.

### Why Gemini 2.5 Flash?
1. **Sub-second Latency**: In fulfillment centers, speed is critical. Gemini 2.5 Flash offers low token latency, allowing predictions to update instantly as operators transition orders between stages.
2. **Native JSON Mode**: The model supports structured JSON response formatting natively. By passing `responseMimeType: "application/json"`, we guarantee the output conforms to our exact database schema.
3. **Reasoning Capabilities**: While standard mathematical formulas can estimate a date, Gemini provides human-like operational reasoning. It explains *why* an order is at risk (e.g., compounding delays from out-of-house sourcing combined with custom lens-coating queues).

---

## 3. API Integration & Prompt Strategy

The backend queries the Vertex AI model endpoint:
`https://us-central1-aiplatform.googleapis.com/v1/projects/{project_id}/locations/us-central1/publishers/google/models/gemini-2.5-flash:generateContent`

### Context Injection
To ensure prediction accuracy, we compile a rich contextual object for the target order:
* **Prescription complexity**: Spherical (SPH) and Cylindrical (CYL) bounds.
* **Fulfillment metrics**: Current stage, elapsed stage time, and total SLA limits (e.g., 48 hours for Single Vision vs. 120 hours for Progressives).
* **Operational variables**: Material indexes (e.g., 1.67, 1.74 requires out-of-house sourcing), premium coatings, and past QC failures.
* **Real-time shop status**: Active lab-wide bottlenecks (e.g., courier delay, lab backing, or coating oven queue).

### Model Prompt Structure
We instruct the model to analyze the data and return a JSON payload:
```json
{
  "predicted_remaining_hours": 34.5,
  "breach_probability": 72.0,
  "reasoning": "• Out-of-house progressive lens requires 48 hours to source.\n• Active lab backing adds a 2.0x bottleneck multiplier to lens edging."
}
```

---

## 4. Resiliency & Fallback Mechanics

To ensure the application remains fully operational if Vertex AI credentials are not provided or Google Cloud is unreachable:
* **Credential Loading**: The server searches for `GOOGLE_APPLICATION_CREDENTIALS.json` in the workspace root. If found, it fetches and refreshes access tokens dynamically.
* **Predictive Heuristics Fallback**: If authorization fails, `ai_helper.py` falls back to a deterministic local scheduler (`Simulator.get_heuristic_remaining_hours`). This calculates remaining hours based on historical stage duration queues and logs structured reasoning.
* **Verification Alerts**: Pushes warnings to the team via mock WhatsApp SMS logs and Email layouts rendered directly in the console and dashboard feed.
