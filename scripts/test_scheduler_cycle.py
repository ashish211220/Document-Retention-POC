import asyncio
import os
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

# Configure logging to see output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from app.sync.scheduler import _run_poll_cycle
from app.services.graph_auth_service import graph_auth_service

async def run():
    print("Getting token and running a single scheduler poll cycle...")
    try:
        token = graph_auth_service.get_access_token()
        await _run_poll_cycle(token)
        print("Cycle completed.")
    except Exception as e:
        print(f"Exception during cycle: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run())
