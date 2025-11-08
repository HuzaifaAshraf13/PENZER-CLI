from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables.")

try:
    client = genai.Client(api_key=GEMINI_API_KEY)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Hello! Can you confirm that my API key works?"
    )

    # Extract the text from response
    if hasattr(response, "content") and response.content:
        text_output = "".join([part.text for part in response.content])
    else:
        text_output = "No text returned."

    print("Gemini API test successful!")
    print("Response:", text_output)

except Exception as e:
    print("API Key test failed:", e)
