// Global variables for dashboard state
let systemTime = null;
let activeOrders = [];
let inventoryRules = null;
let inventoryStock = {};
let alerts = [];

// Helper to format SPH/CYL power as keys matching Python '+.2f' format
function formatPowerKey(sph, cyl) {
    const format = n => (n >= 0 ? '+' : '') + parseFloat(n).toFixed(2);
    return `${format(sph)}_${format(cyl)}`;
}

// Initialize dashboard
document.addEventListener("DOMContentLoaded", () => {
    // Load config and initial data
    loadSimulatorState();
    loadDashboardData();
    loadInventoryMatrix();
    loadAlertLogs();

    // Add event listeners for filters
    document.getElementById("filter-status").addEventListener("change", loadDashboardData);
    document.getElementById("filter-lens-type").addEventListener("change", loadDashboardData);
    document.getElementById("filter-store").addEventListener("change", loadDashboardData);
    document.getElementById("search-box").addEventListener("input", filterOrdersTable);

    // Create modal button triggers
    document.getElementById("btn-open-create-modal").addEventListener("click", () => {
        openModal("create-order-modal");
        runLivePrescriptionValidation();
    });
});

// Modal controls
function openModal(id) {
    document.getElementById(id).classList.add("active");
}

function closeModal(id) {
    document.getElementById(id).classList.remove("active");
}

// Fetch current simulator state (clock, active bottlenecks)
async function loadSimulatorState() {
    try {
        const res = await fetch("/api/simulator");
        const state = await res.json();

        systemTime = state.system_time;

        // Update clock UI
        const formattedTime = new Date(systemTime).toLocaleString('en-US', {
            dateStyle: 'medium',
            timeStyle: 'short'
        });
        document.getElementById("virtual-clock").textContent = formattedTime;

        // Update bottleneck switch check-states
        document.getElementById("toggle-sourcing-delay").checked = state.is_bottleneck_sourcing;
        document.getElementById("toggle-lab-delay").checked = state.is_bottleneck_lab;
        document.getElementById("toggle-coating-delay").checked = state.is_bottleneck_coating;
    } catch (err) {
        console.error("Error loading simulator state:", err);
    }
}

// Fetch all orders and calculate metrics
async function loadDashboardData() {
    const status = document.getElementById("filter-status").value;
    const lensType = document.getElementById("filter-lens-type").value;
    const store = document.getElementById("filter-store").value;

    let url = "/api/orders?";
    if (status) url += `status=${encodeURIComponent(status)}&`;
    if (lensType) url += `lens_type=${encodeURIComponent(lensType)}&`;
    if (store) url += `store_location=${encodeURIComponent(store)}&`;

    try {
        const res = await fetch(url);
        activeOrders = await res.json();

        renderOrdersTable(activeOrders);
        calculateKpis(activeOrders);
    } catch (err) {
        console.error("Error loading orders:", err);
    }
}

// Render Orders Table
function renderOrdersTable(orders) {
    const tbody = document.getElementById("orders-table-body");
    tbody.innerHTML = "";

    if (orders.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 30px;">No matching orders found.</td></tr>`;
        return;
    }

    orders.forEach(o => {
        const tr = document.createElement("tr");

        // SLA remaining hours calculation
        const due = new Date(o.sla_due_at);
        const sys = new Date(systemTime);
        const remSecs = (due - sys) / 1000;
        const remHours = remSecs / 3600;

        let slaText = "";
        let slaClass = "";

        if (o.stage === "Delivered") {
            slaText = "Delivered";
            slaClass = "sla-on-track";
        } else if (remHours < 0) {
            slaText = `Breached (-${Math.abs(remHours).toFixed(1)}h)`;
            slaClass = "sla-breached";
        } else if (remHours <= 12) {
            slaText = `Urgent (${remHours.toFixed(1)}h left)`;
            slaClass = "sla-risk";
        } else {
            slaText = `${remHours.toFixed(1)}h left`;
            slaClass = "sla-on-track";
        }

        // Sourcing badge class
        let sourcingClass = "badge-inhouse";
        if (o.sourcing_status.includes("Out-of-House")) {
            sourcingClass = "badge-sourced";
        }

        // AI predictions details
        let predictionText = "--";
        let predictionClass = "";

        if (o.stage === "Delivered") {
            predictionText = "Delivered";
        } else if (o.tat_prediction) {
            const predHours = o.tat_prediction.predicted_remaining_hours;
            const prob = o.tat_prediction.breach_probability;

            predictionText = `${predHours.toFixed(1)}h remaining`;
            if (prob > 60) {
                predictionClass = "sla-breached";
                predictionText += ` (${prob.toFixed(0)}% breach risk)`;
            } else if (prob > 30) {
                predictionClass = "sla-risk";
                predictionText += ` (${prob.toFixed(0)}% risk)`;
            } else {
                predictionClass = "sla-on-track";
            }
        }

        // Custom specification text
        const rx = o.prescription;
        const spec = `
            <div><strong>${o.lens_type}</strong> (Index: ${o.lens_index})</div>
            <div style="font-size:0.75rem; color: var(--text-secondary);">
                R: SPH ${rx.sph_od > 0 ? '+' : ''}${rx.sph_od.toFixed(2)} | CYL ${rx.cyl_od.toFixed(2)}<br>
                L: SPH ${rx.sph_os > 0 ? '+' : ''}${rx.sph_os.toFixed(2)} | CYL ${rx.cyl_os.toFixed(2)}<br>
                Coating: ${o.coating} | Frame: ${o.frame.model}
            </div>
        `;

        // History Timeline indicator
        const isQCFailed = o.qc_fail_count > 0;
        const stageContent = `
            <div class="stage-tag">
                <span class="stage-dot" style="background-color: ${o.stage === 'Delivered' ? 'var(--status-green)' : (isQCFailed ? 'var(--status-red)' : 'var(--brand-gold)')}"></span>
                <span>${o.stage}</span>
            </div>
            ${isQCFailed ? `<div style="font-size: 0.65rem; color: var(--status-red); font-weight: 500;">Failed QC x${o.qc_fail_count}</div>` : ''}
        `;

        tr.innerHTML = `
            <td><strong>${o.order_id}</strong></td>
            <td>
                <div><strong>${o.customer_name}</strong></div>
                <div style="font-size: 0.75rem; color: var(--text-secondary);">${o.source}</div>
            </td>
            <td>${spec}</td>
            <td><span class="badge ${sourcingClass}">${o.sourcing_status}</span></td>
            <td>${stageContent}</td>
            <td class="${slaClass}">${slaText}</td>
            <td class="${predictionClass}">${predictionText}</td>
            <td>
                <button class="btn-action-sm" onclick="openStatusModal('${o.order_id}')">Manage</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

// Client-side search filtering
function filterOrdersTable() {
    const query = document.getElementById("search-box").value.toLowerCase();
    const rows = document.querySelectorAll("#orders-table-body tr");

    rows.forEach(r => {
        const text = r.textContent.toLowerCase();
        if (text.includes(query) || text.includes("no matching")) {
            r.style.display = "";
        } else {
            r.style.display = "none";
        }
    });
}

// Calculate KPIs
function calculateKpis(orders) {
    if (orders.length === 0) {
        document.getElementById("kpi-active-orders").textContent = "0";
        document.getElementById("kpi-breach-rate").textContent = "0%";
        document.getElementById("kpi-sourcing-ratio").textContent = "0%";
        document.getElementById("kpi-qc-pass-rate").textContent = "100%";
        return;
    }

    // 1. Active Orders Count (Stages not Delivered)
    const active = orders.filter(o => o.stage !== "Delivered");
    document.getElementById("kpi-active-orders").textContent = active.length;
    document.getElementById("kpi-active-orders-sub").textContent = `${orders.filter(o => o.stage === "Delivered").length} orders successfully delivered`;

    // 2. SLA Breach Rate (Active orders already breached or with breach risk > 50%)
    let breachOrRiskCount = 0;
    active.forEach(o => {
        const due = new Date(o.sla_due_at);
        const sys = new Date(systemTime);
        const remHours = (due - sys) / 3600000;

        if (remHours < 0) {
            breachOrRiskCount++;
        } else if (o.tat_prediction && o.tat_prediction.breach_probability >= 50) {
            breachOrRiskCount++;
        }
    });

    const breachRate = active.length > 0 ? (breachOrRiskCount / active.length) * 100 : 0;
    document.getElementById("kpi-breach-rate").textContent = `${breachRate.toFixed(0)}%`;
    document.getElementById("kpi-breach-rate-sub").textContent = `${breachOrRiskCount} active orders at high risk or breached`;

    // 3. Sourcing Ratio (In House Allocated / Total Orders)
    const inHouse = orders.filter(o => o.sourcing_status === "In-House (Allocated)");
    const sourcingRatio = orders.length > 0 ? (inHouse.length / orders.length) * 100 : 0;
    document.getElementById("kpi-sourcing-ratio").textContent = `${sourcingRatio.toFixed(0)}%`;

    // 4. QC Pass Rate
    // Formula: Total successes / Total inspection reviews
    // We treat each order reaching or completing QC as 1 pass, but if they failed, that adds a check count.
    let qcFailures = 0;
    let qcSuccesses = 0;

    orders.forEach(o => {
        qcFailures += o.qc_fail_count || 0;
        const reachedQc = ["QC Check", "Ready for Dispatch", "Shipped", "Delivered"].includes(o.stage);
        if (reachedQc) {
            qcSuccesses++;
        }
    });

    const totalQcChecks = qcSuccesses + qcFailures;
    const qcPassRate = totalQcChecks > 0 ? (qcSuccesses / totalQcChecks) * 100 : 95.0; // Seed baseline

    document.getElementById("kpi-qc-pass-rate").textContent = `${qcPassRate.toFixed(0)}%`;
    document.getElementById("kpi-qc-pass-rate-sub").textContent = `${qcFailures} laboratory re-cutting loops logged`;
}

// Fetch and render inventory grid
async function loadInventoryMatrix() {
    try {
        const res = await fetch("/api/inventory");
        const data = await res.json();

        inventoryRules = data.rules;
        inventoryStock = data.stock;

        renderInventoryMatrixTable(data.sph_values, data.cyl_values);
        calculateInventoryInsights();
    } catch (err) {
        console.error("Error loading inventory:", err);
    }
}

// Compute metrics dynamically from the inventory stock mapping
function calculateInventoryInsights() {
    if (!inventoryStock) return;

    let totalUnits = 0;
    let lowStockCount = 0;

    Object.values(inventoryStock).forEach(qty => {
        totalUnits += qty;
        if (qty > 0 && qty < 3) {
            lowStockCount++;
        }
    });

    const totalEl = document.getElementById("inv-total-units");
    const lowEl = document.getElementById("inv-low-stock-count");

    if (totalEl) totalEl.textContent = totalUnits.toLocaleString();
    if (lowEl) lowEl.textContent = lowStockCount;
}

// Render 2D Inventory Matrix
function renderInventoryMatrixTable(sphValues, cylValues) {
    const table = document.getElementById("inventory-matrix-table");
    table.innerHTML = "";

    // Header row (CYL Powers)
    const headerRow = document.createElement("tr");
    headerRow.innerHTML = `<th class="axis-label">SPH \\ CYL</th>`;
    cylValues.forEach(cyl => {
        const th = document.createElement("th");
        th.textContent = cyl.toFixed(2);
        headerRow.appendChild(th);
    });
    table.appendChild(headerRow);

    // Power rows (SPH values)
    sphValues.forEach(sph => {
        const tr = document.createElement("tr");
        const sphTh = document.createElement("td");
        sphTh.className = "sph-header";
        sphTh.textContent = (sph > 0 ? '+' : '') + sph.toFixed(2);
        tr.appendChild(sphTh);

        cylValues.forEach(cyl => {
            const key = formatPowerKey(sph, cyl);
            const td = document.createElement("td");
            td.className = "matrix-cell";

            // Check if within standard in house rule constraints
            const inHouseSph = (inventoryRules.sph_min <= sph && sph <= inventoryRules.sph_max);
            const inHouseCyl = (inventoryRules.cyl_min <= cyl && cyl <= inventoryRules.cyl_max);
            const inHouseRule = inHouseSph && inHouseCyl;

            const stock = inventoryStock[key] !== undefined ? inventoryStock[key] : 0;

            td.textContent = stock;

            if (!inHouseRule) {
                td.classList.add("cell-out-of-house");
                td.title = `SPH: ${sph.toFixed(2)}, CYL: ${cyl.toFixed(2)} - Out-of-house prescription (Sourced)`;
            } else if (stock === 0) {
                td.classList.add("cell-out-of-house");
                td.title = `SPH: ${sph.toFixed(2)}, CYL: ${cyl.toFixed(2)} - Out of Stock (Requires vendor)`;
            } else if (stock < 3) {
                td.classList.add("cell-low-stock");
                td.title = `SPH: ${sph.toFixed(2)}, CYL: ${cyl.toFixed(2)} - Low Stock (${stock} left)`;
            } else {
                td.classList.add("cell-in-house");
                td.title = `SPH: ${sph.toFixed(2)}, CYL: ${cyl.toFixed(2)} - In-house allocated (${stock} available)`;
            }

            // Allow clicking matrix cell to trigger restock modal
            td.addEventListener("click", () => openRestockModal(key, sph, cyl, stock));

            tr.appendChild(td);
        });

        table.appendChild(tr);
    });
}

// Live validation check in order form
function runLivePrescriptionValidation() {
    const sphOD = parseFloat(document.getElementById("rx-sph-od").value) || 0;
    const cylOD = parseFloat(document.getElementById("rx-cyl-od").value) || 0;
    const sphOS = parseFloat(document.getElementById("rx-sph-os").value) || 0;
    const cylOS = parseFloat(document.getElementById("rx-cyl-os").value) || 0;

    const index = parseFloat(document.getElementById("order-lens-index").value);
    const coating = document.getElementById("order-coating").value;
    const banner = document.getElementById("rx-live-validation-banner");

    if (!inventoryRules) {
        banner.textContent = "Loading guidelines...";
        return;
    }

    // Check material index
    if (!inventoryRules.indexes.includes(index)) {
        banner.textContent = `🚨 Sourced Material: Lens Index ${index} is super-thin and must be sourced externally (+48 hours).`;
        banner.className = "live-validation-result sourced";
        return;
    }

    // Check coatings
    if (!inventoryRules.coatings.includes(coating)) {
        banner.textContent = `🚨 Sourced Coating: Premium coating "${coating}" requires special laboratory processing and vendor supply (+48 hours).`;
        banner.className = "live-validation-result sourced";
        return;
    }

    // Check power ranges
    const odInHouse = (inventoryRules.sph_min <= sphOD && sphOD <= inventoryRules.sph_max) &&
        (inventoryRules.cyl_min <= cylOD && cylOD <= inventoryRules.cyl_max);

    const osInHouse = (inventoryRules.sph_min <= sphOS && sphOS <= inventoryRules.sph_max) &&
        (inventoryRules.cyl_min <= cylOS && cylOS <= inventoryRules.cyl_max);

    if (!odInHouse || !osInHouse) {
        banner.textContent = `🚨 Sourced Powers: Rx power exceeds in-house limits (SPH: ${inventoryRules.sph_min.toFixed(2)} to +${inventoryRules.sph_max.toFixed(2)} | CYL: ${inventoryRules.cyl_min.toFixed(2)} to ${inventoryRules.cyl_max.toFixed(2)}). Must be sourced (+48 hours).`;
        banner.className = "live-validation-result sourced";
        return;
    }

    // Check stock
    const keyOD = formatPowerKey(sphOD, cylOD);
    const keyOS = formatPowerKey(sphOS, cylOS);

    const stockOD = inventoryStock[keyOD] !== undefined ? inventoryStock[keyOD] : 0;
    const stockOS = inventoryStock[keyOS] !== undefined ? inventoryStock[keyOS] : 0;

    if (stockOD === 0 || stockOS === 0) {
        banner.textContent = `⚠️ Sourced Spher/Cyl Power: Power falls within range but stock is currently depleted (OD: ${stockOD} left, OS: ${stockOS} left). Sourcing from laboratory supplier (+48 hours).`;
        banner.className = "live-validation-result sourced";
    } else {
        banner.textContent = `✅ In-house Available: Lenses and options are fully stocked (OD: ${stockOD} units, OS: ${stockOS} units). Immediate laboratory dispatch.`;
        banner.className = "live-validation-result inhouse";
    }
}

// Intake New Order Form Submission
async function submitNewOrder(e) {
    e.preventDefault();

    const payload = {
        customer_name: document.getElementById("order-customer-name").value,
        source: document.getElementById("order-store-location").value,
        lens_type: document.getElementById("order-lens-type").value,
        lens_index: document.getElementById("order-lens-index").value,
        coating: document.getElementById("order-coating").value,
        frame_model: document.getElementById("order-frame-model").value,
        sph_od: parseFloat(document.getElementById("rx-sph-od").value) || 0,
        cyl_od: parseFloat(document.getElementById("rx-cyl-od").value) || 0,
        axis_od: parseInt(document.getElementById("rx-axis-od").value) || 0,
        sph_os: parseFloat(document.getElementById("rx-sph-os").value) || 0,
        cyl_os: parseFloat(document.getElementById("rx-cyl-os").value) || 0,
        axis_os: parseInt(document.getElementById("rx-axis-os").value) || 0
    };

    const btn = document.querySelector("#create-order-form button[type='submit']");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Registering...";
    btn.style.opacity = "0.7";

    try {
        const res = await fetch("/api/orders", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            const createdOrder = await res.json();
            closeModal("create-order-modal");
            document.getElementById("create-order-form").reset();
            // Reload all components
            await loadSimulatorState();
            await loadDashboardData();
            await loadInventoryMatrix();
            showToast("Order registered successfully!");

            // If prediction source is "Initial", start smart polling to fetch refined prediction
            if (createdOrder.tat_prediction && createdOrder.tat_prediction.source === "Local Predictive Heuristics (Initial)") {
                startPredictionPolling(createdOrder.order_id);
            }
        } else {
            const err = await res.json();
            alert(`Error: ${err.error}`);
        }
    } catch (err) {
        console.error("Error creating order:", err);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
        btn.style.opacity = "";
    }
}

// Open Status/Timeline Management Modal
async function openStatusModal(orderId) {
    openModal("status-modal");

    document.getElementById("status-modal-loading").style.display = "block";
    document.getElementById("status-modal-content").style.display = "none";

    try {
        const res = await fetch(`/api/orders/${orderId}`);
        const o = await res.json();

        document.getElementById("status-modal-loading").style.display = "none";
        document.getElementById("status-modal-content").style.display = "block";

        // Populate modal variables
        document.getElementById("update-order-id").value = o.order_id;
        document.getElementById("detail-customer-name").textContent = o.customer_name;
        document.getElementById("detail-order-id-frame").textContent = `${o.order_id} — ${o.frame.model} (${o.frame.style})`;

        // Update stage transition form select
        document.getElementById("update-stage-select").value = o.stage;
        document.getElementById("update-delay-reason").value = "";

        // Render AI predictions
        const aiRemaining = document.getElementById("ai-pred-remaining");
        const aiProb = document.getElementById("ai-pred-probability");
        const aiReason = document.getElementById("ai-pred-reasoning");
        const aiSource = document.getElementById("ai-pred-source");

        if (o.stage === "Delivered") {
            aiRemaining.textContent = "Delivered";
            aiRemaining.className = "p-val on-schedule";
            aiProb.textContent = "0%";
            aiProb.className = "p-val on-schedule";
            aiReason.textContent = "Order has successfully completed its operations lifecycle.";
            aiSource.textContent = "Operations Log";
        } else if (o.tat_prediction) {
            const pred = o.tat_prediction;
            aiRemaining.textContent = `${pred.predicted_remaining_hours.toFixed(1)} Hours`;
            aiProb.textContent = `${pred.breach_probability.toFixed(0)}%`;
            aiReason.innerText = pred.reasoning;
            aiSource.textContent = pred.source || "GCP Vertex AI";

            // Color styles
            if (pred.breach_probability >= 50) {
                aiRemaining.className = "p-val at-risk";
                aiProb.className = "p-val at-risk";
            } else {
                aiRemaining.className = "p-val on-schedule";
                aiProb.className = "p-val on-schedule";
            }
        } else {
            aiRemaining.textContent = "Estimating...";
            aiProb.textContent = "0%";
            aiReason.textContent = "Generating Vertex AI prediction report...";
        }

        // Populate Timeline log
        const timeline = document.getElementById("detail-timeline-container");
        timeline.innerHTML = "";

        o.history.forEach((h, index) => {
            const div = document.createElement("div");
            div.className = "timeline-item";
            if (index === o.history.length - 1) {
                div.classList.add("active");
            }

            const time = new Date(h.timestamp).toLocaleString();

            div.innerHTML = `
                <span class="timeline-dot"></span>
                <span class="timeline-stage">${h.stage}</span>
                <span class="timeline-time">${time}</span>
                ${h.reason ? `<span class="timeline-reason">${h.reason}</span>` : ""}
            `;
            timeline.appendChild(div);
        });

        handleStageSelectChange();

    } catch (err) {
        console.error("Error loading order details:", err);
    }
}

// Stage selection change validation (handles custom warnings)
function handleStageSelectChange() {
    const stage = document.getElementById("update-stage-select").value;
    const reasonInput = document.getElementById("update-delay-reason");
    const label = document.querySelector("label[for='update-delay-reason']");

    if (stage === "QC Failed") {
        label.textContent = "Defect Description (Required)";
        reasonInput.placeholder = "e.g. Lens scratched during assembly, axis alignment off by 5 deg";
        reasonInput.required = true;
    } else {
        label.textContent = "Reason for Delay (Optional)";
        reasonInput.placeholder = "Log explanation if stage transition is delayed";
        reasonInput.required = false;
    }
}

// Submit Order Stage transition
async function submitStatusUpdate(e) {
    e.preventDefault();

    const orderId = document.getElementById("update-order-id").value;
    const stage = document.getElementById("update-stage-select").value;
    const delayReason = document.getElementById("update-delay-reason").value;

    if (stage === "QC Failed" && !delayReason) {
        alert("Please log the defect reason for the quality check failure.");
        return;
    }

    const btn = document.querySelector("#update-status-form button[type='submit']");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Committing...";
    btn.style.opacity = "0.7";

    try {
        const res = await fetch(`/api/orders/${orderId}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                stage: stage,
                delay_reason: delayReason
            })
        });

        if (res.ok) {
            const updatedOrder = await res.json();
            closeModal("status-modal");
            // Reload views
            await loadSimulatorState();
            await loadDashboardData();
            await loadInventoryMatrix();
            await loadAlertLogs();
            showToast("Fulfillment status committed!");

            // If prediction source is "Initial", start smart polling to fetch refined prediction
            if (updatedOrder.tat_prediction && updatedOrder.tat_prediction.source === "Local Predictive Heuristics (Initial)") {
                startPredictionPolling(orderId);
            }
        } else {
            const err = await res.json();
            alert(`Error updating status: ${err.error}`);
        }
    } catch (err) {
        console.error("Error updating status:", err);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
        btn.style.opacity = "";
    }
}

// Open restock matrix cell modal
function openRestockModal(key, sph, cyl, currentQty) {
    document.getElementById("restock-cell-key").value = key;
    document.getElementById("restock-cell-label").textContent = `Update Stock for SPH: ${sph.toFixed(2)}, CYL: ${cyl.toFixed(2)}`;
    document.getElementById("restock-quantity").value = currentQty;
    openModal("restock-modal");
}

// Submit Restock modification
async function submitRestock(e) {
    e.preventDefault();

    const key = document.getElementById("restock-cell-key").value;
    const qty = parseInt(document.getElementById("restock-quantity").value) || 0;

    try {
        const res = await fetch("/api/inventory/stock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                key: key,
                quantity: qty
            })
        });

        if (res.ok) {
            closeModal("restock-modal");
            await loadInventoryMatrix();
            await loadDashboardData(); // In case availability changes status
            showToast("Inventory stock updated!");
        }
    } catch (err) {
        console.error("Error updating stock:", err);
    }
}

// Advance simulator virtual clock ticks
async function advanceSimTime(hours) {
    try {
        const res = await fetch("/api/simulator/tick", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ hours: hours })
        });

        if (res.ok) {
            await loadSimulatorState();
            await loadDashboardData();
            await loadInventoryMatrix();
            await loadAlertLogs();
            showToast(`Advanced simulated time by ${hours} hours!`, "info");
        }
    } catch (err) {
        console.error("Error advancing simulation:", err);
    }
}

// Toggle laboratory bottlenecks
async function toggleBottleneck(key) {
    const sourcing = document.getElementById("toggle-sourcing-delay").checked;
    const lab = document.getElementById("toggle-lab-delay").checked;
    const coating = document.getElementById("toggle-coating-delay").checked;

    const payload = {};
    payload[key] = (key === 'is_bottleneck_sourcing') ? sourcing : ((key === 'is_bottleneck_lab') ? lab : coating);

    try {
        const res = await fetch("/api/simulator/bottleneck", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            await loadSimulatorState();
            await loadDashboardData(); // Recalculates risk with bottleneck enabled
            showToast("Simulator bottlenecks updated!", "info");
        }
    } catch (err) {
        console.error("Error setting bottleneck:", err);
    }
}

// Fetch and render operational alerts
async function loadAlertLogs() {
    try {
        const res = await fetch("/api/alerts");
        const alertsList = await res.json();

        const container = document.getElementById("alerts-feed-container");
        const badge = document.getElementById("alerts-count-badge");

        // Filter out double alerts in feed view for user cleanliness (only show unique message list)
        // Reverse array to show newest first
        alertsList.reverse();

        badge.textContent = `${alertsList.length} Alerts`;

        if (alertsList.length === 0) {
            container.innerHTML = `<div style="text-align: center; color: var(--text-secondary); font-size: 0.8rem; padding: 20px;">No active alerts logged.</div>`;
            return;
        }

        container.innerHTML = "";
        alertsList.forEach(a => {
            const div = document.createElement("div");
            div.className = "alert-item";

            const time = new Date(a.timestamp).toLocaleString();
            const channelClass = a.channel.toLowerCase();

            div.innerHTML = `
                <div class="alert-meta">
                    <span class="alert-channel ${channelClass}">${a.channel}</span>
                    <span>${time}</span>
                </div>
                <div class="alert-text">${a.message}</div>
            `;
            container.appendChild(div);
        });
    } catch (err) {
        console.error("Error loading alerts:", err);
    }
}

// Toast Notifications helper
function showToast(message, type = "success") {
    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        container.className = "toast-container";
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;

    let icon = "✓";
    if (type === "error") icon = "✗";
    if (type === "warning") icon = "⚠";
    if (type === "info") icon = "ℹ";

    toast.innerHTML = `
        <span class="toast-icon">${icon}</span>
        <span class="toast-message">${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => toast.classList.add("show"), 10);

    setTimeout(() => {
        toast.classList.remove("show");
        toast.classList.add("hide");
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Poll order API to fetch the refined prediction once the background worker finishes
function startPredictionPolling(orderId) {
    let attempts = 0;
    const maxAttempts = 6;
    const interval = 1000; // 1 second

    const poll = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts) {
            clearInterval(poll);
            return;
        }

        try {
            const res = await fetch(`/api/orders/${orderId}`);
            if (res.ok) {
                const order = await res.json();
                if (order.tat_prediction && order.tat_prediction.source !== "Local Predictive Heuristics (Initial)") {
                    clearInterval(poll);
                    // Update our activeOrders array with the refined prediction
                    const idx = activeOrders.findIndex(o => o.order_id === orderId);
                    if (idx !== -1) {
                        activeOrders[idx] = order;
                        renderOrdersTable(activeOrders);
                        calculateKpis(activeOrders);
                    }
                    console.log(`Successfully polished prediction for order ${orderId} in attempt ${attempts}`);
                }
            }
        } catch (err) {
            console.error("Error polling order prediction:", err);
            clearInterval(poll);
        }
    }, interval);
}

