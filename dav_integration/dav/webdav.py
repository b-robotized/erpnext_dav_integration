# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import xml.etree.ElementTree as ET

from .session import DavSession

DAV_NS = {"d": "DAV:"}

@dataclass
class WebDavItem:
    href: str
    display_name: Optional[str]
    is_collection: bool
    etag: Optional[str]
    content_length: Optional[int]
    content_type: Optional[str]
    last_modified: Optional[str]

class WebDavClient:
    """Minimal WebDAV client (PROPFIND listing + GET/PUT)."""

    def __init__(self, session: DavSession):
        self.session = session

    def propfind(self, absolute_path: str, depth: int = 1) -> List[WebDavItem]:
        body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getetag/>
    <d:getcontentlength/>
    <d:getcontenttype/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>"""

        headers = {
            "Depth": str(depth),
            "Content-Type": "application/xml; charset=utf-8",
        }
        r = self.session.request("PROPFIND", absolute_path, data=body.encode("utf-8"), headers=headers)
        if r.status_code not in (207, 200):
            raise RuntimeError(f"PROPFIND failed: {r.status_code} {r.text[:300]}")

        root = ET.fromstring(r.text)
        items: List[WebDavItem] = []

        for resp in root.findall("d:response", DAV_NS):
            href = (resp.findtext("d:href", default="", namespaces=DAV_NS) or "").strip()

            prop = resp.find(".//d:prop", DAV_NS)
            if prop is None:
                continue

            display_name = prop.findtext("d:displayname", default=None, namespaces=DAV_NS)
            etag = prop.findtext("d:getetag", default=None, namespaces=DAV_NS)
            cl = prop.findtext("d:getcontentlength", default=None, namespaces=DAV_NS)
            ct = prop.findtext("d:getcontenttype", default=None, namespaces=DAV_NS)
            lm = prop.findtext("d:getlastmodified", default=None, namespaces=DAV_NS)

            is_collection = prop.find("d:resourcetype/d:collection", DAV_NS) is not None

            content_length = None
            if cl:
                try:
                    content_length = int(cl)
                except Exception:
                    content_length = None

            items.append(
                WebDavItem(
                    href=href,
                    display_name=display_name,
                    is_collection=is_collection,
                    etag=etag,
                    content_length=content_length,
                    content_type=ct,
                    last_modified=lm,
                )
            )

        return items

    def get(self, absolute_path: str) -> bytes:
        r = self.session.request("GET", absolute_path)
        r.raise_for_status()
        return r.content

    def put(self, absolute_path: str, content: bytes, content_type: str = "application/octet-stream") -> bool:
        r = self.session.request("PUT", absolute_path, data=content, headers={"Content-Type": content_type})
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"PUT failed: {r.status_code} {r.text[:300]}")
        return True
