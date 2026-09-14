import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("HUBSPOT_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Create two companies with similar names but different phone/location
# This tests the REVIEW confidence tier (possible separate branch locations)

print("Creating branch location test companies...")

# Company 1: Ridgeline HVAC - North
ridgeline_north = {
    "name": "Ridgeline HVAC - North",
    "domain": "ridgelinehvac.com",
    "phone": "512-555-5000",
    "city": "Waco",
    "state": "TX"
}
r = requests.post(
    "https://api.hubapi.com/crm/v3/objects/companies",
    headers=headers,
    json={"properties": ridgeline_north}
)
print(f"Created Ridgeline HVAC - North: {r.status_code}")

# Company 2: Ridgeline HVAC - South
ridgeline_south = {
    "name": "Ridgeline HVAC - South",
    "domain": "ridgeline-hvac.com",
    "phone": "512-555-6000",
    "city": "San Antonio",
    "state": "TX"
}
r = requests.post(
    "https://api.hubapi.com/crm/v3/objects/companies",
    headers=headers,
    json={"properties": ridgeline_south}
)
print(f"Created Ridgeline HVAC - South: {r.status_code}")

print("\nDone! Run clean_hubspot_data.py to see REVIEW confidence tier.")
