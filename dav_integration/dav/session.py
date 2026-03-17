# -*- coding: utf-8 -*-
from __future__ import annotations

import requests
from typing import Dict, Optional

class DavSession:
    """Small wrapper around requests.Session with sane defaults."""

    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout_s: int = 30,
        verify_tls: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.verify_tls = verify_tls
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": "dav_integration/0.0.1 (Frappe)",
                "Accept": "*/*",
            }
        )
        if headers:
            self.s.headers.update(headers)

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def request(self, method: str, path: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout_s)
        kwargs.setdefault("verify", self.verify_tls)
        return self.s.request(method, self.url(path), **kwargs)
