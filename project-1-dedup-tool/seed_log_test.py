import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("HUBSPOT_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Single contact for testing audit log functionality
contact = {"firstname": "logtest", "lastname": "Verify", "phone": "512-555-0400"}

r = requests.post(
    "https://api.hubapi.com/crm/v3/objects/contacts",
    headers=headers,
    json={"properties": contact}
)
print(r.status_code, contact["firstname"], contact["lastname"])
