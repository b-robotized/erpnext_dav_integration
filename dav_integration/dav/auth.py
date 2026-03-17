# -*- coding: utf-8 -*-
import base64
from typing import Dict

def build_basic_auth_headers(username: str, password: str) -> Dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}
