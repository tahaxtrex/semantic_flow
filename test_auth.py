import os
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("ANTHROPIC_API_KEY")
print(f"Key loaded: {key[:4]}...{key[-4:] if key else 'None'}")

client = Anthropic(api_key=key)

try:
    message = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=10,
        messages=[
            {"role": "user", "content": "Hello"}
        ]
    )
    print("Success:")
    print(message.content[0].text)
except Exception as e:
    print("Error:")
    print(e)
