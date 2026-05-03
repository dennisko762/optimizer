from __future__ import annotations

from typing import Any, Optional

import httpx


class SimBriefClientError(RuntimeError):
    pass


class SimBriefClient:
    BASE_URL = "https://www.simbrief.com/api/xml.fetcher.php"

    def __init__(self, timeout_seconds: float = 15.0):
        self.timeout_seconds = timeout_seconds

    async def fetch_latest_ofp(
        self,
        *,
        username: Optional[str] = None,
        user_id: Optional[str] = None,
        static_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Holt den letzten SimBrief OFP als JSON.

        Entweder username oder user_id setzen.
        static_id ist optional, falls du später API-generierte Flights eindeutig ziehen willst.
        """

        if not username and not user_id:
            raise ValueError("Either username or user_id must be provided.")

        params: dict[str, str] = {
            "json": "v2",
        }

        if username:
            params["username"] = username

        if user_id:
            params["userid"] = str(user_id)

        if static_id:
            params["static_id"] = static_id

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(self.BASE_URL, params=params)

        if response.status_code != 200:
            raise SimBriefClientError(
                f"SimBrief request failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise SimBriefClientError(
                f"SimBrief returned non-JSON response: {response.text[:500]}"
            ) from exc

        if isinstance(data, dict) and "fetch" in data:
            fetch = data.get("fetch") or {}
            status = str(fetch.get("status", "")).lower()
            if status == "error":
                raise SimBriefClientError(str(fetch.get("message", "Unknown SimBrief error")))

        return data