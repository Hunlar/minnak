import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ARQ request settings
TIMEOUT_SECONDS = 120
CONNECT_TIMEOUT_SECONDS = 10

# ARQ query limit
MAX_QUERY_CHARS = 4000

# Turkish error messages
BUSY_TEXT = "Şu anda çok fazla kişi benimle konuşuyor. Bir dakika sonra tekrar dene."
UNAVAILABLE_TEXT = "Şu anda cevap veremiyorum. Lütfen daha sonra tekrar dene."

# Minnak language instruction
TURKISH_INSTRUCTION = (
    "ÇOK ÖNEMLİ: Sen Minnak adlı bir sohbet asistanısın. "
    "Her koşulda ve istisnasız yalnızca Türkçe konuşmalısın. "
    "Kullanıcı İngilizce, Arapça veya başka herhangi bir dilde yazsa bile "
    "cevabını daima Türkçe ver. "
    "Başka bir dilde cevap verme. "
    "Türkçe karakterleri doğru kullan. "
    "Samimi, doğal, akıcı ve insan gibi sohbet et. "
    "Gerektiğinde espri yap, gerektiğinde ciddi ol. "
    "Bu dil kuralını bütün sohbet boyunca koru.\n\n"
)


class LunaError(Exception):
    """ARQ cevap vermediğinde kullanılan hata."""


def _json_body(response: httpx.Response) -> dict:
    """ARQ JSON cevabını okur."""
    try:
        body = response.json()
    except ValueError:
        return {}

    if isinstance(body, dict):
        return body

    return {}


class ArqClient:

    def __init__(
        self,
        api_url: str,
        api_key: str,
        timeout: float = TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers={"X-API-KEY": api_key},
            timeout=httpx.Timeout(
                timeout,
                connect=CONNECT_TIMEOUT_SECONDS
            ),
            transport=transport,
        )

    async def close(self):
        await self._client.aclose()

    async def luna(self, text: str, user_id: int) -> str:
        """Minnak: Türkçe sohbet asistanı."""

        query = TURKISH_INSTRUCTION + text

        try:
            response = await self._client.get(
                "luna",
                params={
                    "query": query,
                    "id": user_id
                }
            )

        except httpx.HTTPError as error:
            logger.warning(
                "ARQ bağlantı hatası: %s",
                type(error).__name__
            )
            raise LunaError(UNAVAILABLE_TEXT) from error

        body = _json_body(response)
        result = body.get("result")

        if response.status_code == 429:
            raise LunaError(BUSY_TEXT)

        if response.status_code in (401, 403):
            logger.error(
                "ARQ_API_KEY geçersiz. @ARQRobot üzerinden kontrol edin."
            )
            raise LunaError(UNAVAILABLE_TEXT)

        answered = (
            response.status_code < 400
            and body.get("ok") is True
        )

        if not answered or not isinstance(result, str) or not result.strip():
            logger.warning(
                "ARQ /luna hatası: HTTP %s, %r",
                response.status_code,
                result
            )
            raise LunaError(UNAVAILABLE_TEXT)

        return result.strip()
