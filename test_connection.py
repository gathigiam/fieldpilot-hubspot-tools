import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("HUBSPOT_TOKEN")

headers = {"Authorization": f"Bearer {token}"}
response = requests.get(
    "https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
    headers=headers
)

print(response.status_code)
print(response.json())