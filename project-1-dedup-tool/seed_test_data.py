import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("HUBSPOT_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

contacts = [
    {"firstname": "juan", "lastname": "MARTINEZ", "phone": "512-555-0182"},
    {"firstname": "SARA", "lastname": "diaz", "phone": "(512) 555 0199"},
    {"firstname": "mike", "lastname": "o'brien", "phone": "5125550123"},
    {"firstname": "Tom", "lastname": "Nguyen", "phone": "+1 512.555.0147"},
    {"firstname": "Angela", "lastname": "Brooks", "phone": "+15125550188"},
]

companies = [
    {"name": "ABC Heating & Air", "domain": "abcheatingair.com"},
    {"name": "abc heating and air llc", "domain": "abcheatingair.com"},
    {"name": "Cool Breeze HVAC", "domain": "coolbreezehvac.com"},
    {"name": "COOL BREEZE HVAC SERVICES", "domain": "coolbreeze-hvac.com"},
    {"name": "Sunrise Plumbing", "domain": "sunriseplumbing.com"},
]

for c in contacts:
    r = requests.post(
        "https://api.hubapi.com/crm/v3/objects/contacts",
        headers=headers,
        json={"properties": c}
    )
    print(r.status_code, c["firstname"], c["lastname"])

for co in companies:
    r = requests.post(
        "https://api.hubapi.com/crm/v3/objects/companies",
        headers=headers,
        json={"properties": co}
    )
    print(r.status_code, co["name"])