import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("HUBSPOT_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# First, fetch existing companies to get IDs for the four we need to update
print("Fetching existing companies...")
response = requests.get(
    "https://api.hubapi.com/crm/v3/objects/companies",
    headers=headers,
    params={"limit": 100, "properties": "name,domain"}
)

if response.status_code != 200:
    print(f"ERROR: {response.status_code}")
    print(response.text)
    exit(1)

companies = response.json().get("results", [])

# Find the IDs for the four companies we need to update
abc_heating_id = None
abc_heating_llc_id = None
cool_breeze_id = None
cool_breeze_services_id = None

for company in companies:
    props = company.get("properties", {})
    name = props.get("name", "")
    domain = props.get("domain", "")

    if name == "ABC Heating & Air" and domain == "abcheatingair.com":
        abc_heating_id = company["id"]
    elif name == "abc heating and air llc" and domain == "abcheatingair.com":
        abc_heating_llc_id = company["id"]
    elif name == "Cool Breeze HVAC" and domain == "coolbreezehvac.com":
        cool_breeze_id = company["id"]
    elif name == "COOL BREEZE HVAC SERVICES" and domain == "coolbreeze-hvac.com":
        cool_breeze_services_id = company["id"]

print(f"ABC Heating & Air ID: {abc_heating_id}")
print(f"abc heating and air llc ID: {abc_heating_llc_id}")
print(f"Cool Breeze HVAC ID: {cool_breeze_id}")
print(f"COOL BREEZE HVAC SERVICES ID: {cool_breeze_services_id}")

# Update ABC Heating pair with matching phone/location (should score HIGH)
if abc_heating_id:
    r = requests.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{abc_heating_id}",
        headers=headers,
        json={"properties": {"phone": "512-555-1000", "city": "Austin", "state": "TX"}}
    )
    print(f"Updated ABC Heating & Air: {r.status_code}")

if abc_heating_llc_id:
    r = requests.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{abc_heating_llc_id}",
        headers=headers,
        json={"properties": {"phone": "512-555-1000", "city": "Austin", "state": "TX"}}
    )
    print(f"Updated abc heating and air llc: {r.status_code}")

# Update Cool Breeze pair with matching phone/location (should score HIGH)
if cool_breeze_id:
    r = requests.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{cool_breeze_id}",
        headers=headers,
        json={"properties": {"phone": "512-555-2000", "city": "Round Rock", "state": "TX"}}
    )
    print(f"Updated Cool Breeze HVAC: {r.status_code}")

if cool_breeze_services_id:
    r = requests.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{cool_breeze_services_id}",
        headers=headers,
        json={"properties": {"phone": "512-555-2000", "city": "Round Rock", "state": "TX"}}
    )
    print(f"Updated COOL BREEZE HVAC SERVICES: {r.status_code}")

# Create two new companies with similar domains but different names/phone/location (should score LOW)
print("\nCreating false-positive test companies...")

# Company 1: Titan Mechanical Services
titan_mech = {
    "name": "Titan Mechanical Services",
    "domain": "titanmech.com",
    "phone": "512-555-3000",
    "city": "Dallas",
    "state": "TX"
}
r = requests.post(
    "https://api.hubapi.com/crm/v3/objects/companies",
    headers=headers,
    json={"properties": titan_mech}
)
print(f"Created Titan Mechanical Services: {r.status_code}")

# Company 2: Blue Ridge Cooling Co (similar domain but completely different)
blue_ridge = {
    "name": "Blue Ridge Cooling Co",
    "domain": "titan-mech.com",
    "phone": "512-555-4000",
    "city": "Houston",
    "state": "TX"
}
r = requests.post(
    "https://api.hubapi.com/crm/v3/objects/companies",
    headers=headers,
    json={"properties": blue_ridge}
)
print(f"Created Blue Ridge Cooling Co: {r.status_code}")

print("\nDone! Run clean_hubspot_data.py to see confidence scoring.")
