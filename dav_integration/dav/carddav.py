# -*- coding: utf-8 -*-
"""CardDAV module (stub).

Next steps:
- addressbook discovery (PROPFIND on addressbooks home)
- contact sync using ETag
- parse vCard and map to ERPNext Contact
"""

from __future__ import annotations
from .session import DavSession

class CardDavClient:
    def __init__(self, session: DavSession):
        self.session = session
