import os
from langfuse import Langfuse

os.environ["LANGFUSE_BASE_URL"] = "http://34.169.71.235:3000"
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-4f61ec1f-aad8-4887-9de3-df0cff20d9ea"
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-eb699848-816b-4f43-a109-a57d64e9af00"

client = Langfuse(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    base_url=os.environ["LANGFUSE_BASE_URL"],
)

if client.auth_check():
    print("Connection successful!")
else:
    print("Connection failed.")
