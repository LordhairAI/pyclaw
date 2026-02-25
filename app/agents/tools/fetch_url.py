import requests
from pathlib import Path
from langchain.tools import tool
import logging
#from dotenv import load_dotenv
#load_dotenv()
logger = logging.getLogger("uvicorn.error")
@tool
def fetch_url(url: str) -> str:
    """Fetch text content from a URL"""
    response = requests.get(url, timeout=10.0)
    response.raise_for_status()
    logger.info(f"Status code: {response.text}")
    logger.info(f"Fetched {len(response.text)} characters from {url}")
    return response.text