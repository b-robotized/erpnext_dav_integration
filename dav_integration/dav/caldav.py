# -*- coding: utf-8 -*-
"""CalDAV module (stub).

Next steps:
- calendar discovery (PROPFIND on calendars home)
- event sync using ETag
- parse iCalendar (VEVENT) and map to ERPNext Event
"""

from __future__ import annotations
from .session import DavSession

class CalDavClient:
    def __init__(self, session: DavSession):
        self.session = session
