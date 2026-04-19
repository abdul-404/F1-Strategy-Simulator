import pickle
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict
import os
import re
import requests
from simulate import run_simulation

DYNAMIC_DRIVER_CACHE = {}

# --- Pydantic Models for Request/Response ---

class SimulationRequest(BaseModel):
    driver_name: str
    race_name: str
    pit_stop_loss: float
    grid_position: int = 0  # Optional: driver's grid position (0 = pole, field_size = last)
    weather: str = 'DRY'    # Optional: weather condition
    use_ga_optimizer: bool = True  # Optional: use Genetic Algorithm (default) or brute force
    use_monte_carlo: bool = True   # Optional: enable Monte Carlo analysis
    sc_laps: List[List[int]] = []  # Optional: safety car info [[start_lap, duration], ...]

class LapsResponse(BaseModel):
    total_laps: int

class APIDataResponse(BaseModel):
    drivers: List[str]
    races: List[str]

# --- Load Database on Startup ---

DB_FILE = "strategy_database.pkl"
strategy_database: Dict = {}

def load_database():
    """Loads the strategy database from the .pkl file."""
    global strategy_database
    try:
        with open(DB_FILE, 'rb') as f:
            strategy_database = pickle.load(f)
        print(f"[OK] Master database loaded successfully from {DB_FILE}")
    except FileNotFoundError:
        print(f"[ERROR] '{DB_FILE}' not found.")
        print("Please ensure 'precompute.py' has run and the file is in the same directory.")
        strategy_database = {} # Ensure it's an empty dict
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred loading the database: {e}")
        strategy_database = {}

# --- FastAPI App Initialization ---

app = FastAPI(title="F1 Strategy Simulator API")

# Add CORS middleware to allow the frontend to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (good for local dev)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# --- Serve Static Files (Lap Visualizer Component) ---
@app.get("/lap-visualizer.js", response_class=FileResponse)
async def get_lap_visualizer_js():
    """Serves the lap visualizer JavaScript component."""
    if os.path.exists("lap-visualizer.js"):
        return FileResponse("lap-visualizer.js", media_type="application/javascript")
    else:
        raise HTTPException(status_code=404, detail="lap-visualizer.js not found")

@app.get("/lap-visualizer.css", response_class=FileResponse)
async def get_lap_visualizer_css():
    """Serves the lap visualizer CSS styling."""
    if os.path.exists("lap-visualizer.css"):
        return FileResponse("lap-visualizer.css", media_type="text/css")
    else:
        raise HTTPException(status_code=404, detail="lap-visualizer.css not found")

app.mount("/race-tracks", StaticFiles(directory="Race Tracks"), name="race-tracks")

@app.on_event("startup")
async def startup_event():
    """Run on server startup."""
    load_database()

# --- API Endpoints ---

@app.get("/", response_class=HTMLResponse)
async def get_frontend(request: Request):
    """Serves the main index.html file."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html not found</h1><p>Please add the frontend file to your project directory.</p>", status_code=404)

@app.get("/api-data", response_model=APIDataResponse)
async def get_api_data():
    """Provides the lists of available drivers and races for dropdowns."""
    if not strategy_database:
        raise HTTPException(status_code=503, detail="Database not loaded. Run precompute.py and restart server.")
    
    try:
        drivers = sorted([driver for driver in strategy_database['driver_performance'].keys() if driver != 'GRID_AVG'])
        races = sorted(strategy_database['track_models'].keys())
        
        return APIDataResponse(drivers=drivers, races=races)
    except KeyError as e:
        raise HTTPException(status_code=500, detail=f"Database is missing expected key: {e}")

@app.get("/get-laps/{race_name}", response_model=LapsResponse)
async def get_laps_for_race(race_name: str):
    """Provides the total lap count for a selected race."""
    if not strategy_database:
        raise HTTPException(status_code=503, detail="Database not loaded.")
        
    try:
        total_laps = strategy_database['track_models'][race_name]['total_laps']
        
        # --- FIX: Convert numpy.int64 to standard Python int ---
        return LapsResponse(total_laps=int(total_laps))
    
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Race '{race_name}' not found in database.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")


@app.post("/simulate")
async def simulate_strategy_endpoint(request: SimulationRequest):
    """Runs the main simulation logic with advanced features."""
    if not strategy_database:
        raise HTTPException(status_code=503, detail="Database not loaded. Run precompute.py and restart server.")

    print(f"--- Simulating for {request.driver_name} at {request.race_name} ---")
    print(f"    Weather: {request.weather}, Grid Position: {request.grid_position}")
    print(f"    GA Optimizer: {request.use_ga_optimizer}, Monte Carlo: {request.use_monte_carlo}")

    try:
        # Convert safety car laps from list to tuple format
        sc_laps = [(sc[0], sc[1]) for sc in request.sc_laps] if request.sc_laps else None

        results = run_simulation(
            driver_name=request.driver_name,
            race_name=request.race_name,
            pit_stop_loss=request.pit_stop_loss,
            db=strategy_database,
            use_ga_optimizer=request.use_ga_optimizer,
            use_monte_carlo=request.use_monte_carlo,
            grid_position=request.grid_position,
            weather=request.weather,
            sc_laps=sc_laps
        )

        if "error" in results:
            raise HTTPException(status_code=404, detail=results["error"])

        return results

    except Exception as e:
        print(f"❌ ERROR during simulation: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

import fastf1 as ff1

# Cache to store track maps in memory so subsequent clicks are instant
TRACK_MAP_CACHE = {}

# Change the route to only expect race_name
@app.get("/api/track-layout/{race_name}")
def get_track_layout(race_name: str):
    
    # 1. Hardcode the year internally so the frontend doesn't have to care about it
    year = 2023 
    
    cache_key = f"{year}_{race_name.lower()}"
    
    if cache_key in TRACK_MAP_CACHE:
        return TRACK_MAP_CACHE[cache_key]
        
    try:
        ff1.Cache.enable_cache('fastf1_cache') 
        
        # FastF1 uses our internal 'year' variable
        session = ff1.get_session(year, race_name, 'Q') 
        session.load(telemetry=True, weather=False, messages=False)
        
        fastest_lap = session.laps.pick_fastest()
        telemetry = fastest_lap.get_telemetry()
        
        clean_telemetry = telemetry.dropna(subset=['X', 'Y'])
        
        x_coords = clean_telemetry['X'].iloc[::2].tolist()
        y_coords = clean_telemetry['Y'].iloc[::2].tolist()
        
        TRACK_MAP_CACHE[cache_key] = {
            "x": x_coords, 
            "y": y_coords,
            "metadata": {
                "driver": fastest_lap['Driver'],
                "lap_time": str(fastest_lap['LapTime'])
            }
        }
        
        return TRACK_MAP_CACHE[cache_key]
        
    except Exception as e:
        print(f"Error loading track map for {race_name}: {e}")
        raise HTTPException(status_code=500, detail="Could not load track data")
    
@app.get("/api/driver-info/{driver_input}")
def get_driver_info_dynamic(driver_input: str):
    search_term = driver_input.lower().strip()
    if search_term in DYNAMIC_DRIVER_CACHE:
        return DYNAMIC_DRIVER_CACHE[search_term]
        
    headers = {"User-Agent": "F1StrategySimulator/1.1 (Educational Project)"}
    
    try:
        # 1. Identity Check
        drivers_req = requests.get("https://api.jolpi.ca/ergast/f1/2024/drivers.json", headers=headers, timeout=5)
        drivers_data = drivers_req.json()
        
        driver_id, driver_code, full_name, nationality = None, "F1", "", "Unknown"
        for d in drivers_data['MRData']['DriverTable']['Drivers']:
            d_full = f"{d['givenName']} {d['familyName']}"
            if search_term in [d_full.lower(), d.get('code', '').lower(), d['driverId'].lower()]:
                driver_id, driver_code, full_name, nationality = d['driverId'], d.get('code', 'F1'), d_full, d['nationality']
                break
        
        if not full_name:
            return {"error": "Driver not found."}

        # 2. Wikipedia Deep Scrape
        wiki_title = full_name.replace(" ", "_")
        wiki_url = f"https://en.wikipedia.org/w/api.php?action=parse&page={wiki_title}&prop=wikitext&format=json&redirects=1"
        wiki_res = requests.get(wiki_url, headers=headers, timeout=5).json()
        wikitext = wiki_res["parse"]["wikitext"]["*"]

        def get_stat(field):
            # NEW LOGIC: Look for the field name, skip the template characters {{...}}, 
            # and grab the digits that follow.
            pattern = rf"\|\s*{field}\s*=\s*(?:\{{{{2}}.*?\{{{{2}}|[^0-9])*(\d+)"
            match = re.search(pattern, wikitext, re.IGNORECASE)
            
            if match:
                return int(match.group(1))
            
            # Fallback: Search for the number anywhere in that specific line
            line_match = re.search(rf"\|\s*{field}\s*=\s*.*?(\d+)", wikitext, re.IGNORECASE)
            return int(line_match.group(1)) if line_match else 0

        # Special logic for Entries (starts)
        entries = get_stat("Entries")
        if entries == 0: entries = get_stat("races") # fallback for older formatting

        final_profile = {
            "code": driver_code.upper(),
            "name": full_name,
            "team": "F1 Grid", 
            "country": nationality,
            "championships": get_stat("Championships"),
            "wins": get_stat("wins"),
            "podiums": get_stat("podiums"),
            "poles": get_stat("poles"),
            "starts": entries
        }
        
        DYNAMIC_DRIVER_CACHE[search_term] = final_profile
        return final_profile

    except Exception as e:
        return {"error": f"Stats Error: {str(e)}"}

# --- Uvicorn runner (for local testing) ---
if __name__ == "__main__":
    import uvicorn
    print("--- Starting F1 Strategy Simulator API ---")
    print("Access the frontend at: http://127.0.0.1:8000")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

