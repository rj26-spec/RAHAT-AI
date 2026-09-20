# RAHAT AI
AI-Powered Emergency Intelligence & Response Coordination Platform

## Core demo flow
Citizen report -> AI classification -> severity/risk score -> duplicate check -> nearby resource recommendation -> responder dashboard -> dispatch.

## Run on Windows PowerShell
1. Open PowerShell in this folder.
2. Create environment:
   python -m venv venv
3. Activate:
   .\venv\Scripts\Activate.ps1
   If blocked:
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\venv\Scripts\Activate.ps1
4. Install:
   pip install -r requirements.txt
5. Optional AI:
   copy .env.example .env
   Open .env and add OPENROUTER_API_KEY=your_key
6. Start:
   uvicorn main:app --reload
7. Open:
   http://127.0.0.1:8000

## Demo coordinates
The app starts with sample resources around Cuttack.
Use "Use Demo Location" in the report form if browser geolocation is unavailable.

## If the AI API is unavailable
RAHAT AI automatically uses a local rule-based fallback, so the demo still works.

## Suggested 3-minute demo
1. Report: "Two people injured in a road accident near college gate, one unconscious and traffic is blocked."
2. Submit.
3. Show CRITICAL severity and risk score.
4. Show duplicate detection and nearby ambulance recommendation.
5. Open dashboard and map.
6. Dispatch the recommended ambulance.
7. Explain that the prototype can later connect to real ambulance/GIS/traffic systems.
