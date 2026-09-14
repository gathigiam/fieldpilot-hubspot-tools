import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("HUBSPOT_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Additional test contacts for Mc/Mac prefix edge case testing
contacts = [
    {"firstname": "mcdonald", "lastname": "Smith", "phone": "512-555-0201"},
    {"firstname": "macy", "lastname": "Jones", "phone": "(512) 555-0202"},
]

for c in contacts:
    r = requests.post(
        "https://api.hubapi.com/crm/v3/objects/contacts",
        headers=headers,
        json={"properties": c}
    )
    print(r.status_code, c["firstname"], c["lastname"])
