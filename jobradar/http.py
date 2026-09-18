from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST"])
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return s


SESSION = _session()


def get_json(url: str, **kw):
    r = SESSION.get(url, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: dict, **kw):
    r = SESSION.post(url, json=payload, timeout=30, **kw)
    r.raise_for_status()
    return r.json()
