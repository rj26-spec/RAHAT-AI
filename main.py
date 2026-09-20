import os
import re
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "rahat.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RAHAT AI")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT NOT NULL,
        incident_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        risk_score INTEGER NOT NULL,
        people_affected INTEGER DEFAULT 1,
        lat REAL NOT NULL,
        lng REAL NOT NULL,
        status TEXT DEFAULT 'NEW',
        ai_reason TEXT,
        image_url TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        lat REAL NOT NULL,
        lng REAL NOT NULL,
        status TEXT DEFAULT 'AVAILABLE'
    );

    CREATE TABLE IF NOT EXISTS dispatches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id INTEGER NOT NULL,
        resource_id INTEGER NOT NULL,
        eta_minutes INTEGER NOT NULL,
        status TEXT DEFAULT 'DISPATCHED',
        created_at TEXT NOT NULL
    );
    """)

    count = con.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    if count == 0:
        seed = [
            ("Ambulance A", "AMBULANCE", 20.2961, 85.8245, "AVAILABLE"),
            ("Ambulance B", "AMBULANCE", 20.2920, 85.8180, "AVAILABLE"),
            ("Ambulance C", "AMBULANCE", 20.3010, 85.8300, "BUSY"),
            ("Fire Unit 1", "FIRE", 20.2985, 85.8210, "AVAILABLE"),
            ("Traffic Unit 1", "TRAFFIC", 20.2940, 85.8265, "AVAILABLE"),
        ]
        con.executemany(
            "INSERT INTO resources(name,resource_type,lat,lng,status) VALUES(?,?,?,?,?)",
            seed,
        )
    con.commit()
    con.close()


init_db()


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * r * math.asin(math.sqrt(a))


def local_ai(text):
    t = text.lower()
    incident_type = "OTHER"
    if any(x in t for x in ["accident", "crash", "collision", "vehicle"]):
        incident_type = "ROAD_ACCIDENT"
    elif any(x in t for x in ["fire", "smoke", "burning", "flame"]):
        incident_type = "FIRE"
    elif any(x in t for x in ["flood", "waterlogging", "water logged", "drain overflow"]):
        incident_type = "FLOOD"
    elif any(x in t for x in ["unconscious", "bleeding", "injured", "medical", "heart"]):
        incident_type = "MEDICAL"
    elif any(x in t for x in ["electric", "wire", "shock", "transformer"]):
        incident_type = "ELECTRICAL"
    elif any(x in t for x in ["building", "collapse", "wall", "structure"]):
        incident_type = "INFRASTRUCTURE"

    score = 35
    reasons = []
    critical_words = ["unconscious", "severe", "critical", "trapped", "explosion", "fire", "bleeding"]
    high_words = ["injured", "accident", "blocked", "smoke", "collapse", "flood"]
    if any(x in t for x in critical_words):
        score += 40
        reasons.append("critical-risk language detected")
    if any(x in t for x in high_words):
        score += 20
        reasons.append("high-risk incident indicators detected")
    m = re.search(r"(\d+)\s*(people|persons|victims|injured)", t)
    people = int(m.group(1)) if m else 1
    if people >= 3:
        score += 10
        reasons.append("multiple people reported")
    score = max(10, min(99, score))

    severity = "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MEDIUM" if score >= 40 else "LOW"
    if not reasons:
        reasons.append("baseline incident risk assessment")

    return {
        "incident_type": incident_type,
        "severity": severity,
        "risk_score": score,
        "people_affected": people,
        "reason": "; ".join(reasons),
    }


def ai_analyze(text):
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        return local_ai(text)

    model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free")
    prompt = f"""Analyze this emergency report for a prototype.
Return ONLY valid JSON with:
incident_type, severity, risk_score, people_affected, reason.
incident_type must be one of ROAD_ACCIDENT,FIRE,FLOOD,MEDICAL,ELECTRICAL,INFRASTRUCTURE,OTHER.
severity must be LOW,MEDIUM,HIGH,CRITICAL.
risk_score is 0-100.
Do not invent facts. Report:
{text}"""
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=20,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.S)
        data = json.loads(match.group(0) if match else content)
        data["risk_score"] = max(0, min(100, int(data["risk_score"])))
        data["people_affected"] = max(1, int(data.get("people_affected", 1)))
        return data
    except Exception:
        return local_ai(text)


def find_duplicate(lat, lng, incident_type):
    con = db()
    rows = con.execute(
        """SELECT id,lat,lng,incident_type,status
           FROM incidents
           WHERE status IN ('NEW','VERIFIED','DISPATCHED')
           ORDER BY id DESC LIMIT 100"""
    ).fetchall()
    con.close()
    for row in rows:
        distance = haversine_km(lat, lng, row["lat"], row["lng"])
        if distance <= 0.7 and row["incident_type"] == incident_type:
            return {"id": row["id"], "distance_km": round(distance, 2)}
    return None


def recommend_resources(lat, lng, incident_type, limit=3):
    wanted = []
    if incident_type in ("MEDICAL", "ROAD_ACCIDENT"):
        wanted = ["AMBULANCE", "TRAFFIC"]
    elif incident_type == "FIRE":
        wanted = ["FIRE"]
    else:
        wanted = ["TRAFFIC", "AMBULANCE"]

    con = db()
    placeholders = ",".join(["?"] * len(wanted))
    rows = con.execute(
        f"""SELECT * FROM resources
            WHERE status='AVAILABLE' AND resource_type IN ({placeholders})""",
        wanted,
    ).fetchall()
    con.close()

    out = []
    for row in rows:
        d = haversine_km(lat, lng, row["lat"], row["lng"])
        eta = max(2, round(d * 3.0))
        out.append({
            "id": row["id"],
            "name": row["name"],
            "resource_type": row["resource_type"],
            "distance_km": round(d, 2),
            "eta_minutes": eta,
        })
    return sorted(out, key=lambda x: x["eta_minutes"])[:limit]


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/dashboard")
def dashboard():
    con = db()
    incidents = [dict(x) for x in con.execute(
        "SELECT * FROM incidents ORDER BY id DESC LIMIT 50"
    ).fetchall()]
    resources = [dict(x) for x in con.execute(
        "SELECT * FROM resources ORDER BY id"
    ).fetchall()]
    dispatches = [dict(x) for x in con.execute(
        "SELECT * FROM dispatches ORDER BY id DESC LIMIT 20"
    ).fetchall()]
    stats = {
        "total": con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
        "critical": con.execute("SELECT COUNT(*) FROM incidents WHERE severity='CRITICAL'").fetchone()[0],
        "active": con.execute("SELECT COUNT(*) FROM incidents WHERE status IN ('NEW','VERIFIED','DISPATCHED')").fetchone()[0],
        "available": con.execute("SELECT COUNT(*) FROM resources WHERE status='AVAILABLE'").fetchone()[0],
    }
    con.close()
    return {"stats": stats, "incidents": incidents, "resources": resources, "dispatches": dispatches}


@app.post("/api/report")
async def report(
    description: str = Form(...),
    lat: float = Form(...),
    lng: float = Form(...),
    image: object = None,
):
    analysis = ai_analyze(description)
    duplicate = find_duplicate(lat, lng, analysis["incident_type"])

    con = db()
    now = datetime.now().isoformat(timespec="seconds")
    cur = con.execute(
        """INSERT INTO incidents
        (description,incident_type,severity,risk_score,people_affected,lat,lng,status,ai_reason,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            description,
            analysis["incident_type"],
            analysis["severity"],
            analysis["risk_score"],
            analysis["people_affected"],
            lat, lng,
            "VERIFIED" if duplicate else "NEW",
            analysis["reason"],
            now,
        ),
    )
    incident_id = cur.lastrowid
    con.commit()
    con.close()

    resources = recommend_resources(lat, lng, analysis["incident_type"])
    return {
        "incident_id": incident_id,
        "analysis": analysis,
        "duplicate": duplicate,
        "resources": resources,
        "message": "Report grouped with a nearby similar incident." if duplicate else "New incident created.",
    }


@app.post("/api/dispatch")
def dispatch(incident_id: int = Form(...), resource_id: int = Form(...)):
    con = db()
    resource = con.execute("SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()
    incident = con.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if not resource or not incident:
        con.close()
        return JSONResponse({"error": "Incident or resource not found"}, status_code=404)
    distance = haversine_km(incident["lat"], incident["lng"], resource["lat"], resource["lng"])
    eta = max(2, round(distance * 3.0))
    con.execute("UPDATE resources SET status='BUSY' WHERE id=?", (resource_id,))
    con.execute("UPDATE incidents SET status='DISPATCHED' WHERE id=?", (incident_id,))
    con.execute(
        "INSERT INTO dispatches(incident_id,resource_id,eta_minutes,status,created_at) VALUES(?,?,?,?,?)",
        (incident_id, resource_id, eta, "DISPATCHED", datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    con.close()
    return {"ok": True, "eta_minutes": eta}


@app.post("/api/reset")
def reset():
    con = db()
    con.execute("DELETE FROM dispatches")
    con.execute("DELETE FROM incidents")
    con.execute("UPDATE resources SET status='AVAILABLE'")
    con.execute("UPDATE resources SET status='BUSY' WHERE name='Ambulance C'")
    con.commit()
    con.close()
    return {"ok": True}
