# cache_data.py
#
# FASTER, PARALLEL VERSION
# Run this script ONCE PER SESSION to download all necessary data
# to the local Colab runtime cache.

import fastf1 as ff1
from datetime import datetime
import warnings
import os
import concurrent.futures

warnings.filterwarnings("ignore")

# --- 1. Define Local Colab Paths ---
CACHE_PATH = "fastf1_cache/"
os.makedirs(CACHE_PATH, exist_ok=True)
# ---------------------------------

# --- 2. Configuration ---
# Use the corrected, reliable years
YEARS_TO_CACHE = [2022, 2023, 2024] 
# Number of parallel downloads. 10 is a good start.
# Increase if you have a fast connection, decrease if you get errors.
MAX_WORKERS = 10
# ---------------------

def cache_event(task):
    """Function to be run in parallel: caches a single event."""
    year, event_name = task
    try:
        print(f"Starting cache for: {year} {event_name}...")
        session = ff1.get_session(year, event_name, 'R')
        session.load(telemetry=False, weather=False, messages=False)
        print(f"✅ Finished caching: {year} {event_name}")
        return True
    except Exception as e:
        if 'SessionNotAvailable' in str(e) or 'has not happened' in str(e):
            print(f"-> Session not available for {year} {event_name}. Skipping.")
        else:
            print(f"-> ❌ ERROR caching {year} {event_name}: {e}. Skipping.")
        return False

def main():
    try:
        ff1.Cache.enable_cache(CACHE_PATH)
        print(f"FastF1 cache enabled at: {CACHE_PATH}")
    except Exception as e:
        print(f"Could not enable FastF1 cache: {e}")
        exit()

    print(f"Starting parallel cache for {YEARS_TO_CACHE} with {MAX_WORKERS} workers.")
    
    # 1. Get a list of all tasks first
    tasks_to_run = []
    for year in YEARS_TO_CACHE:
        print(f"Fetching schedule for {year}...")
        try:
            schedule = ff1.get_event_schedule(year)
            for _, event in schedule.iterrows():
                if event['EventFormat'] != 'testing':
                    tasks_to_run.append((year, event['EventName']))
        except Exception as e:
            print(f"Could not fetch schedule for {year}. Skipping. Error: {e}")

    print(f"\nFound {len(tasks_to_run)} total race events to cache.")
    
    # 2. Run all tasks in a parallel thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # map() runs the cache_event function for every item in tasks_to_run
        list(executor.map(cache_event, tasks_to_run))

    print("\n\n--- ALL DATA CACHING COMPLETE ---")
    print(f"All data is now saved in the local runtime at {CACHE_PATH}")
    print("You can now run 'precompute.py'.")

if __name__ == '__main__':
    main()