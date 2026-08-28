import asyncio
import httpx
from typing import Optional, Dict, Any, Callable

async def safe_http_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    log: Optional[Callable[[str, str], None]] = None,
    data: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = 120,
    retry_interval_seconds: int = 5
) -> Optional[httpx.Response]:
    """
    Executes HTTP request with automatic network failure recovery.
    If Wi-Fi or Internet disconnects, pauses execution, logs real-time status to the UI,
    waits for connection to return, and resumes right where it left off.
    """
    attempt = 0
    was_disconnected = False

    while attempt < max_retries:
        try:
            method_upper = method.upper()
            if method_upper == "POST":
                resp = await client.post(url, data=data, json=json, headers=headers)
            else:
                resp = await client.get(url, headers=headers)

            if was_disconnected and log:
                log("🟢 [NETWORK RESTORED] Internet connection restored! Resuming scraper right where it left off.", "info")
                was_disconnected = False

            return resp

        except (httpx.NetworkError, httpx.TimeoutException, httpx.ConnectError, httpx.RequestError, OSError, ConnectionError) as exc:
            attempt += 1
            was_disconnected = True
            
            if log:
                if attempt == 1:
                    log(f"🔴 [NETWORK WARNING] Internet connection lost or unstable ({type(exc).__name__}). Pausing scraper...", "warning")
                elif attempt % 3 == 0 or attempt == 2:
                    log(f"🔄 [NETWORK RETRY] Waiting for Wi-Fi / Internet to reconnect (Retry attempt {attempt}/{max_retries})...", "warning")

            await asyncio.sleep(retry_interval_seconds)

    if log:
        log("❌ [NETWORK ERROR] Unable to reconnect after maximum retries. Saving collected progress.", "error")
    return None
