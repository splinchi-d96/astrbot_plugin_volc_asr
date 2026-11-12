"""共享的 aiohttp 会话：懒加载、复用连接池，插件卸载时统一关闭。"""

from __future__ import annotations

import aiohttp


class HttpClient:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
