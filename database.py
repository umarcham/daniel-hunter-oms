import os
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import psycopg2 for Postgres support
try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.extensions
    from psycopg2.extras import Json
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    # Stub Json class for SQLite compatibility if psycopg2 is absent
    class Json:
        def __init__(self, adapted):
            self.adapted = adapted

    # Stub psycopg2 namespace to prevent NameErrors in SQLite mode
    class PsyExtraStub:
        RealDictCursor = "RealDictCursor"
    class PsyStub:
        extras = PsyExtraStub()
    psycopg2 = PsyStub()


if HAS_PSYCOPG2:
    # Register default typecast to convert all database Numeric/Decimal types to float
    DEC2FLOAT = psycopg2.extensions.new_type(
        psycopg2.extensions.DECIMAL.values,
        'DEC2FLOAT',
        lambda value, cur: float(value) if value is not None else None
    )
    psycopg2.extensions.register_type(DEC2FLOAT)

# Load connection configurations from environment variables
load_dotenv()

# SPH and CYL standard ranges
SPH_VALUES = [round(-6.0 + 0.25 * i, 2) for i in range(41)] # -6.00 to +4.00
CYL_VALUES = [round(-2.0 + 0.25 * i, 2) for i in range(9)]   # -2.00 to 0.00

DEFAULT_IN_HOUSE_RANGES = {
    "sph_min": -6.00,
    "sph_max": 4.00,
    "cyl_min": -2.00,
    "cyl_max": 0.00,
    "indexes": [1.56, 1.61],
    "coatings": ["None", "Anti-Reflective"]
}

# Cursor wrapper to emulate psycopg2 cursor behavior for SQLite
class SQLiteCursorWrapper:
    def __init__(self, cursor, return_dict=False):
        self.cursor = cursor
        self.return_dict = return_dict

    def execute(self, sql, params=None):
        # Translate query placeholders from PostgreSQL (%s) to SQLite (?)
        sql = sql.replace("%s", "?").replace(" FOR UPDATE", "").replace("GREATEST", "MAX")
        
        # Handle TRUNCATE emulation for SQLite
        if sql.strip().startswith("TRUNCATE TABLE"):
            for table in ["system_state", "inventory_stock", "orders", "alerts"]:
                self.cursor.execute(f"DELETE FROM {table};")
            return
            
        # Serialize dict/list parameters to json string for SQLite
        if params:
            new_params = []
            for p in params:
                if type(p).__name__ == 'Json':
                    new_params.append(json.dumps(p.adapted))
                elif isinstance(p, (dict, list)):
                    new_params.append(json.dumps(p))
                else:
                    new_params.append(p)
            params = tuple(new_params)
            
        if params is not None:
            self.cursor.execute(sql, params)
        else:
            self.cursor.execute(sql)
            
    def executemany(self, sql, params):
        # Translate placeholder
        sql = sql.replace("%s", "?")
        self.cursor.executemany(sql, params)
            
    def fetchone(self):
        row = self.cursor.fetchone()
        return self._parse_row(row)
        
    def fetchall(self):
        rows = self.cursor.fetchall()
        return [self._parse_row(r) for r in rows]
        
    def _parse_row(self, row):
        if not row:
            return row
            
        if self.return_dict:
            d = {}
            for idx, col in enumerate(self.cursor.description):
                d[col[0]] = row[idx]
            for field in ["prescription", "frame", "history", "tat_prediction"]:
                if field in d and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except Exception:
                        pass
            return d
        else:
            return row
            
    def close(self):
        self.cursor.close()
        
    def __getattr__(self, name):
        return getattr(self.cursor, name)

# Connection wrapper to emulate psycopg2 connection behavior for SQLite
class SQLiteConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn
        
    def cursor(self, cursor_factory=None):
        return_dict = (cursor_factory is not None)
        return SQLiteCursorWrapper(self.conn.cursor(), return_dict=return_dict)
        
    def commit(self):
        self.conn.commit()
        
    def rollback(self):
        self.conn.rollback()
        
    def close(self):
        self.conn.close()
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.__exit__(exc_type, exc_val, exc_tb)

class Database:
    def __init__(self):
        self.mode = "sqlite"
        if HAS_PSYCOPG2:
            try:
                # Test connection to PostgreSQL with 3-second timeout
                host = os.getenv("DB_HOST", "")
                if host.startswith("/"):
                    # Unix socket connection (Cloud Run)
                    conn = psycopg2.connect(
                        host=host,
                        user=os.getenv("DB_USER", "postgres"),
                        password=os.getenv("DB_PASS", ""),
                        dbname=os.getenv("DB_NAME", "postgres"),
                        connect_timeout=3
                    )
                else:
                    # TCP connection with public IP
                    conn = psycopg2.connect(
                        host=host,
                        port=os.getenv("DB_PORT", "5432"),
                        user=os.getenv("DB_USER", "postgres"),
                        password=os.getenv("DB_PASS", ""),
                        dbname=os.getenv("DB_NAME", "postgres"),
                        sslmode="require",
                        connect_timeout=3
                    )
                conn.close()
                self.mode = "postgres"
                logger.info("Successfully connected to Google Cloud SQL (PostgreSQL). Operating in Postgres mode.")
            except Exception as e:
                logger.warning(f"Failed to connect to Google Cloud SQL (PostgreSQL): {e}")
                logger.info("Automatically falling back to local SQLite database ('eluno.db') for zero-setup local execution.")
        else:
            logger.info("psycopg2 is not installed. Operating in local SQLite fallback mode ('eluno.db').")
            
        self._init_db()
        
    def _get_connection(self):
        if self.mode == "sqlite":
            conn = sqlite3.connect("eluno.db")
            return SQLiteConnectionWrapper(conn)
        else:
            try:
                host = os.getenv("DB_HOST", "")
                if host.startswith("/"):
                    # Unix socket connection (Cloud Run)
                    conn = psycopg2.connect(
                        host=host,
                        user=os.getenv("DB_USER", "postgres"),
                        password=os.getenv("DB_PASS", ""),
                        dbname=os.getenv("DB_NAME", "postgres")
                    )
                else:
                    # TCP connection with public IP
                    conn = psycopg2.connect(
                        host=host,
                        port=os.getenv("DB_PORT", "5432"),
                        user=os.getenv("DB_USER", "postgres"),
                        password=os.getenv("DB_PASS", ""),
                        dbname=os.getenv("DB_NAME", "postgres"),
                        sslmode="require",
                        connect_timeout=3
                    )
                psycopg2.extras.register_default_jsonb(conn_or_curs=conn, globally=True)
                return conn
            except Exception as e:
                logger.warning(f"Failed to connect to Google Cloud SQL (PostgreSQL) during operation: {e}. Falling back to SQLite.")
                self.mode = "sqlite"
                conn = sqlite3.connect("eluno.db")
                return SQLiteConnectionWrapper(conn)

    def _init_db(self):
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Create system_state table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                id INT PRIMARY KEY,
                system_time VARCHAR(100) NOT NULL,
                time_multiplier INT NOT NULL,
                is_bottleneck_lab BOOLEAN NOT NULL,
                is_bottleneck_coating BOOLEAN NOT NULL,
                is_bottleneck_sourcing BOOLEAN NOT NULL
            );
        """)
        
        # Create inventory_stock table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory_stock (
                sph_cyl VARCHAR(50) PRIMARY KEY,
                quantity INT NOT NULL
            );
        """)
        
        # Create orders table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id VARCHAR(50) PRIMARY KEY,
                customer_name VARCHAR(255) NOT NULL,
                source VARCHAR(255) NOT NULL,
                lens_type VARCHAR(100) NOT NULL,
                prescription JSONB NOT NULL,
                lens_index NUMERIC(4, 2) NOT NULL,
                coating VARCHAR(100) NOT NULL,
                frame JSONB NOT NULL,
                placed_at VARCHAR(100) NOT NULL,
                sla_hours INT NOT NULL,
                sla_due_at VARCHAR(100) NOT NULL,
                stage VARCHAR(100) NOT NULL,
                sourcing_status VARCHAR(100) NOT NULL,
                qc_fail_count INT NOT NULL,
                history JSONB NOT NULL,
                delay_reason TEXT NOT NULL,
                tat_prediction JSONB
            );
        """)
        
        # Create alerts table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                alert_id VARCHAR(50) PRIMARY KEY,
                order_id VARCHAR(50) NOT NULL,
                timestamp VARCHAR(100) NOT NULL,
                channel VARCHAR(100) NOT NULL,
                message TEXT NOT NULL
            );
        """)
        
        conn.commit()
        
        # Check if tables are empty, and seed them if they are
        cur.execute("SELECT COUNT(*) FROM system_state;")
        if cur.fetchone()[0] == 0:
            self.reset_db(conn, cur)
            
        cur.close()
        conn.close()
        
    def reset_db(self, conn=None, cur=None):
        should_close = False
        if conn is None or cur is None:
            conn = self._get_connection()
            cur = conn.cursor()
            should_close = True
            
        # Truncate all tables
        cur.execute("TRUNCATE TABLE system_state, inventory_stock, orders, alerts CASCADE;")
        
        # Generate initial inventory stock
        inventory_stock = {}
        for sph in SPH_VALUES:
            for cyl in CYL_VALUES:
                key = f"{sph:+.2f}_{cyl:+.2f}"
                if -3.0 <= sph <= 1.0 and -1.0 <= cyl <= 0.0:
                    inventory_stock[key] = 12
                else:
                    inventory_stock[key] = 4
                    
        # Insert stock using batch execution
        if self.mode == "sqlite":
            cur.executemany(
                "INSERT INTO inventory_stock (sph_cyl, quantity) VALUES (?, ?);",
                list(inventory_stock.items())
            )
        else:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO inventory_stock (sph_cyl, quantity) VALUES %s;",
                list(inventory_stock.items())
            )
            
        # Seed system state
        now = datetime.now()
        system_state = {
            "system_time": now.isoformat(),
            "time_multiplier": 1,
            "is_bottleneck_lab": False,
            "is_bottleneck_coating": False,
            "is_bottleneck_sourcing": False
        }
        cur.execute("""
            INSERT INTO system_state (id, system_time, time_multiplier, is_bottleneck_lab, is_bottleneck_coating, is_bottleneck_sourcing)
            VALUES (1, %s, %s, %s, %s, %s);
        """, (system_state["system_time"], system_state["time_multiplier"], system_state["is_bottleneck_lab"], system_state["is_bottleneck_coating"], system_state["is_bottleneck_sourcing"]))
        
        # Seed orders
        frames = [
            {"model": "Emerald-5", "price": 180.00, "style": "Round Gold/Green"},
            {"model": "Astro-3", "price": 245.00, "style": "Square Dark Navy"},
            {"model": "Maestro-1", "price": 185.00, "style": "Square Matte Black"},
            {"model": "Vassili-1", "price": 178.00, "style": "Geometric Gold"}
        ]
        
        seed_data = [
            {
                "customer_name": "Sarah Connor",
                "source": "Bandra Store",
                "lens_type": "Single Vision",
                "prescription": {"sph_od": -2.00, "cyl_od": -0.50, "axis_od": 180, "sph_os": -1.75, "cyl_os": 0.00, "axis_os": 0},
                "lens_index": 1.56,
                "coating": "Anti-Reflective",
                "frame": frames[0],
                "hours_ago": 36,
                "stage": "Lab Processing",
                "history": [
                    {"stage": "Order Placed", "timestamp": (now - timedelta(hours=36)).isoformat(), "reason": ""},
                    {"stage": "Lens Sourcing", "timestamp": (now - timedelta(hours=34)).isoformat(), "reason": ""}
                ]
            },
            {
                "customer_name": "Tony Stark",
                "source": "Colaba Store",
                "lens_type": "Progressive",
                "prescription": {"sph_od": +1.50, "cyl_od": -1.00, "axis_od": 90, "sph_os": +1.75, "cyl_os": -0.75, "axis_os": 85},
                "lens_index": 1.67,
                "coating": "Blue Cut",
                "frame": frames[1],
                "hours_ago": 72,
                "stage": "Coating",
                "history": [
                    {"stage": "Order Placed", "timestamp": (now - timedelta(hours=72)).isoformat(), "reason": ""},
                    {"stage": "Lens Sourcing", "timestamp": (now - timedelta(hours=48)).isoformat(), "reason": "Sourcing out-of-house progressive high-index lens"},
                    {"stage": "Lab Processing", "timestamp": (now - timedelta(hours=24)).isoformat(), "reason": ""}
                ]
            },
            {
                "customer_name": "Bruce Wayne",
                "source": "Online Store",
                "lens_type": "Bifocal",
                "prescription": {"sph_od": -4.50, "cyl_od": -1.50, "axis_od": 120, "sph_os": -4.25, "cyl_os": -1.25, "axis_os": 115},
                "lens_index": 1.61,
                "coating": "None",
                "frame": frames[2],
                "hours_ago": 12,
                "stage": "Lens Sourcing",
                "history": [
                    {"stage": "Order Placed", "timestamp": (now - timedelta(hours=12)).isoformat(), "reason": ""}
                ]
            },
            {
                "customer_name": "Clark Kent",
                "source": "Bandra Store",
                "lens_type": "Single Vision",
                "prescription": {"sph_od": -7.50, "cyl_od": -2.25, "axis_od": 10, "sph_os": -7.25, "cyl_os": -2.00, "axis_os": 15},
                "lens_index": 1.74,
                "coating": "Photochromic",
                "frame": frames[3],
                "hours_ago": 46,
                "stage": "Lab Processing",
                "history": [
                    {"stage": "Order Placed", "timestamp": (now - timedelta(hours=46)).isoformat(), "reason": ""},
                    {"stage": "Lens Sourcing", "timestamp": (now - timedelta(hours=40)).isoformat(), "reason": "Out of house lens sourcing delay"}
                ]
            },
            {
                "customer_name": "Peter Parker",
                "source": "Juhu Store",
                "lens_type": "Single Vision",
                "prescription": {"sph_od": -1.00, "cyl_od": 0.00, "axis_od": 0, "sph_os": -1.00, "cyl_os": 0.00, "axis_os": 0},
                "lens_index": 1.56,
                "coating": "None",
                "frame": frames[0],
                "hours_ago": 40,
                "stage": "Delivered",
                "history": [
                    {"stage": "Order Placed", "timestamp": (now - timedelta(hours=40)).isoformat(), "reason": ""},
                    {"stage": "Lens Sourcing", "timestamp": (now - timedelta(hours=39)).isoformat(), "reason": ""},
                    {"stage": "Lab Processing", "timestamp": (now - timedelta(hours=28)).isoformat(), "reason": ""},
                    {"stage": "QC Check", "timestamp": (now - timedelta(hours=20)).isoformat(), "reason": ""},
                    {"stage": "Ready for Dispatch", "timestamp": (now - timedelta(hours=18)).isoformat(), "reason": ""},
                    {"stage": "Shipped", "timestamp": (now - timedelta(hours=14)).isoformat(), "reason": ""},
                    {"stage": "Delivered", "timestamp": (now - timedelta(hours=2)).isoformat(), "reason": ""}
                ]
            },
            {
                "customer_name": "Diana Prince",
                "source": "Colaba Store",
                "lens_type": "Progressive",
                "prescription": {"sph_od": -3.00, "cyl_od": -0.75, "axis_od": 45, "sph_os": -2.75, "cyl_os": -0.50, "axis_os": 40},
                "lens_index": 1.61,
                "coating": "Anti-Reflective",
                "frame": frames[1],
                "hours_ago": 110,
                "stage": "QC Check",
                "history": [
                    {"stage": "Order Placed", "timestamp": (now - timedelta(hours=110)).isoformat(), "reason": ""},
                    {"stage": "Lens Sourcing", "timestamp": (now - timedelta(hours=108)).isoformat(), "reason": ""},
                    {"stage": "Lab Processing", "timestamp": (now - timedelta(hours=70)).isoformat(), "reason": ""},
                    {"stage": "QC Check", "timestamp": (now - timedelta(hours=50)).isoformat(), "reason": ""},
                    {"stage": "Lab Processing", "timestamp": (now - timedelta(hours=48)).isoformat(), "reason": "QC Failed: Lens alignment off. Looping back for re-edging."},
                    {"stage": "QC Check", "timestamp": (now - timedelta(hours=10)).isoformat(), "reason": ""}
                ],
                "qc_fail_count": 1
            }
        ]
        
        for i, seed in enumerate(seed_data):
            order_id = f"DH-{1000 + i}"
            placed_time = (now - timedelta(hours=seed["hours_ago"])).isoformat()
            
            # SLAs
            sla_hours = 48 if seed["lens_type"] == "Single Vision" else (96 if seed["lens_type"] == "Bifocal" else 120)
            sla_time = ((now - timedelta(hours=seed["hours_ago"])) + timedelta(hours=sla_hours)).isoformat()
            
            # Sourcing
            is_in_house = self._check_prescription_in_house(seed["prescription"], seed["lens_index"], seed["coating"])
            sourcing_status = "In-House (Allocated)" if is_in_house else "Out-of-House (Sourced)"
            
            # Insert order
            cur.execute("""
                INSERT INTO orders (
                    order_id, customer_name, source, lens_type, prescription, lens_index, coating, frame,
                    placed_at, sla_hours, sla_due_at, stage, sourcing_status, qc_fail_count, history, delay_reason, tat_prediction
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                order_id, seed["customer_name"], seed["source"], seed["lens_type"],
                Json(seed["prescription"]), seed["lens_index"], seed["coating"], Json(seed["frame"]),
                placed_time, sla_hours, sla_time, seed["stage"], sourcing_status, seed.get("qc_fail_count", 0),
                Json(seed["history"]), seed["history"][-1].get("reason", "") if seed["history"] else "", None
            ))
            
            if is_in_house:
                k_od = f"{seed['prescription']['sph_od']:+.2f}_{seed['prescription']['cyl_od']:+.2f}"
                k_os = f"{seed['prescription']['sph_os']:+.2f}_{seed['prescription']['cyl_os']:+.2f}"
                cur.execute("UPDATE inventory_stock SET quantity = GREATEST(0, quantity - 1) WHERE sph_cyl IN (%s, %s);", (k_od, k_os))
                
        conn.commit()
        if should_close:
            cur.close()
            conn.close()

    def _check_prescription_in_house(self, rx, index, coating):
        rules = DEFAULT_IN_HOUSE_RANGES
        if index not in rules["indexes"]:
            return False
        if coating not in rules["coatings"]:
            return False
        for eye in ["od", "os"]:
            sph = rx.get(f"sph_{eye}", 0.0)
            cyl = rx.get(f"cyl_{eye}", 0.0)
            if not (rules["sph_min"] <= sph <= rules["sph_max"]):
                return False
            if not (rules["cyl_min"] <= cyl <= rules["cyl_max"]):
                return False
        return True

    def get_orders(self):
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM orders;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    def get_order(self, order_id):
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM orders WHERE order_id = %s;", (order_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None

    def save_orders(self, orders):
        conn = self._get_connection()
        cur = conn.cursor()
        for o in orders:
            cur.execute("""
                INSERT INTO orders (
                    order_id, customer_name, source, lens_type, prescription, lens_index, coating, frame,
                    placed_at, sla_hours, sla_due_at, stage, sourcing_status, qc_fail_count, history, delay_reason, tat_prediction
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id) DO UPDATE SET
                    customer_name = EXCLUDED.customer_name,
                    source = EXCLUDED.source,
                    lens_type = EXCLUDED.lens_type,
                    prescription = EXCLUDED.prescription,
                    lens_index = EXCLUDED.lens_index,
                    coating = EXCLUDED.coating,
                    frame = EXCLUDED.frame,
                    placed_at = EXCLUDED.placed_at,
                    sla_hours = EXCLUDED.sla_hours,
                    sla_due_at = EXCLUDED.sla_due_at,
                    stage = EXCLUDED.stage,
                    sourcing_status = EXCLUDED.sourcing_status,
                    qc_fail_count = EXCLUDED.qc_fail_count,
                    history = EXCLUDED.history,
                    delay_reason = EXCLUDED.delay_reason,
                    tat_prediction = EXCLUDED.tat_prediction;
            """, (
                o["order_id"], o["customer_name"], o["source"], o["lens_type"],
                Json(o["prescription"]), o["lens_index"], o["coating"], Json(o["frame"]),
                o["placed_at"], o["sla_hours"], o["sla_due_at"], o["stage"],
                o["sourcing_status"], o["qc_fail_count"], Json(o["history"]),
                o["delay_reason"], Json(o["tat_prediction"]) if o.get("tat_prediction") else None
            ))
        conn.commit()
        cur.close()
        conn.close()

    def get_inventory(self):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT sph_cyl, quantity FROM inventory_stock;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        stock = {r[0]: r[1] for r in rows}
        return {
            "rules": DEFAULT_IN_HOUSE_RANGES,
            "stock": stock,
            "sph_values": SPH_VALUES,
            "cyl_values": CYL_VALUES
        }

    def update_inventory_stock(self, key, quantity):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO inventory_stock (sph_cyl, quantity) VALUES (%s, %s)
            ON CONFLICT (sph_cyl) DO UPDATE SET quantity = EXCLUDED.quantity;
        """, (key, max(0, quantity)))
        conn.commit()
        cur.close()
        conn.close()

    def allocate_stock(self, prescription):
        """
        Atomically checks if stock is available for prescription SPH/CYL.
        If available, decrements the stock and returns (True, "In-House (Allocated)").
        If not, returns (False, "Out-of-House (Sourced - Out of Stock)").
        """
        k_od = f"{prescription['sph_od']:+.2f}_{prescription['cyl_od']:+.2f}"
        k_os = f"{prescription['sph_os']:+.2f}_{prescription['cyl_os']:+.2f}"
        
        conn = self._get_connection()
        cur = conn.cursor()
        
        try:
            # Select and lock rows for update
            cur.execute("SELECT sph_cyl, quantity FROM inventory_stock WHERE sph_cyl IN (%s, %s) FOR UPDATE;", (k_od, k_os))
            rows = cur.fetchall()
            stock = {r[0]: r[1] for r in rows}
            
            stock_od = stock.get(k_od, 0)
            stock_os = stock.get(k_os, 0)
            
            if stock_od > 0 and stock_os > 0:
                cur.execute("UPDATE inventory_stock SET quantity = quantity - 1 WHERE sph_cyl IN (%s, %s);", (k_od, k_os))
                conn.commit()
                return True, "In-House (Allocated)"
            else:
                conn.rollback()
                return False, "Out-of-House (Sourced - Out of Stock)"
        except Exception:
            conn.rollback()
            return False, "Out-of-House (Sourced - Allocation Error)"
        finally:
            cur.close()
            conn.close()

    def get_system_state(self):
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM system_state WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else {}

    def save_system_state(self, state):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO system_state (id, system_time, time_multiplier, is_bottleneck_lab, is_bottleneck_coating, is_bottleneck_sourcing)
            VALUES (1, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                system_time = EXCLUDED.system_time,
                time_multiplier = EXCLUDED.time_multiplier,
                is_bottleneck_lab = EXCLUDED.is_bottleneck_lab,
                is_bottleneck_coating = EXCLUDED.is_bottleneck_coating,
                is_bottleneck_sourcing = EXCLUDED.is_bottleneck_sourcing;
        """, (state["system_time"], state["time_multiplier"], state["is_bottleneck_lab"], state["is_bottleneck_coating"], state["is_bottleneck_sourcing"]))
        conn.commit()
        cur.close()
        conn.close()

    def log_alert(self, order_id, channel, message):
        conn = self._get_connection()
        cur = conn.cursor()
        
        # Get existing alert IDs to find the maximum suffix (handles deletions and gaps safely)
        cur.execute("SELECT alert_id FROM alerts;")
        rows = cur.fetchall()
        max_num = 9999
        for r in rows:
            # Handle tuple rows for default cursor and dict rows for RealDictCursor
            val = r[0] if isinstance(r, (tuple, list)) else r.get("alert_id")
            if val:
                try:
                    num = int(val.split("-")[1])
                    if num > max_num:
                        max_num = num
                except Exception:
                    pass
        alert_id = f"ALT-{max_num + 1}"
        
        # Get system time
        cur.execute("SELECT system_time FROM system_state WHERE id = 1;")
        row = cur.fetchone()
        system_time = row[0] if isinstance(row, (tuple, list)) else row.get("system_time")
        
        cur.execute("""
            INSERT INTO alerts (alert_id, order_id, timestamp, channel, message)
            VALUES (%s, %s, %s, %s, %s);
        """, (alert_id, order_id, system_time, channel, message))
        conn.commit()
        
        cur.close()
        conn.close()
        
        return {
            "alert_id": alert_id,
            "order_id": order_id,
            "timestamp": system_time,
            "channel": channel,
            "message": message
        }

    def get_alerts(self):
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM alerts;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
