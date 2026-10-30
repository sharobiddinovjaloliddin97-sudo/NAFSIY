import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(Path(__file__).resolve().parent / ".env")
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GROQ_API_KEY is not configured in .env")
    try:
        with Groq(api_key=api_key, timeout=30.0, max_retries=0) as client:
            response = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                messages=[{
                    "role": "user",
                    "content": "Assalomu alaykum! O'zbekcha javob ber.",
                }],
                max_completion_tokens=1024,
            )
        answer = response.choices[0].message.content if response.choices else None
        if not answer:
            raise SystemExit("Groq returned no answer")
        print(answer)
    except APITimeoutError:
        raise SystemExit("Groq request timed out") from None
    except APIStatusError as error:
        raise SystemExit(f"Groq HTTP {error.status_code}; check API key, model and quota") from None
    except APIConnectionError:
        raise SystemExit("Cannot connect to Groq") from None


if __name__ == "__main__":
    main()
