import os

from dotenv import load_dotenv

load_dotenv()

# `.env` carries real LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY/
# LANGFUSE_BASE_URL (needed for live_assistant.py and harness.py), and
# load_dotenv() above pulls in all three for this process too -- pytest
# must never actually trace to Langfuse (mocked fixture data -- fake
# "Bob"/"Carol" requests -- has no business in a real project's
# dashboard, and a sandboxed test environment may not even have
# reliable outbound network access, which previously surfaced as
# confusing background export-retry warnings and slow test runs, not a
# clean failure). Setting this explicitly, unconditionally, is what
# actually enforces "pytest never traces" -- credentials merely being
# present in the environment does not, on its own, prevent Langfuse
# from trying to use them.
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
