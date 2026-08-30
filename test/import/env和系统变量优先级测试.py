import os

from dotenv import load_dotenv

load_dotenv()

print(os.getenv("ITEM_NAME_COLLECTION"))
print(os.getenv("OPENAI_API_KEY"))