import threading
import time

import requests

API_URL = "https://cloud-api.yandex.net/v1/disk"
RETRY_CODES = {429, 500, 502, 503, 504}
PAGE_SIZE = 200
ITEM_FIELDS = (
    "_embedded.items.name,_embedded.items.path,_embedded.items.type,"
    "_embedded.items.size,_embedded.items.md5,_embedded.total"
)


class DiskError(Exception):
    pass


def normalize(path):
    path = path.strip()
    if path.startswith("disk:"):
        path = path[5:]
    return "disk:/" + path.strip("/")


class Disk:
    def __init__(self, token, api_url=API_URL):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._local = threading.local()

    @property
    def session(self):
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["Authorization"] = f"OAuth {self.token}"
            self._local.session = session
        return session

    def _get(self, endpoint, **params):
        url = self.api_url + endpoint
        last = None
        for attempt in range(6):
            try:
                resp = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(2 ** attempt, 20))
                continue
            code = resp.status_code
            if code in RETRY_CODES:
                last = f"HTTP {code}"
                time.sleep(min(2 ** attempt, 20))
                continue
            if code == 401:
                raise DiskError("Токен не принят. Проверьте его и срок действия.")
            if code == 403:
                raise DiskError("Доступ запрещён: у токена нет права на чтение Диска.")
            if code == 404:
                raise DiskError(f"На Диске нет такого пути: {params.get('path', '')}")
            if code >= 400:
                raise DiskError(f"Ошибка API, HTTP {code}: {resp.text[:200]}")
            return resp.json()
        raise DiskError(f"Запрос {endpoint} не удался: {last}")

    def check(self):
        return self._get("")

    def stat(self, path):
        return self._get("/resources", path=path, limit=0, fields="name,path,type,size,md5")

    def listdir(self, path):
        offset = 0
        while True:
            data = self._get(
                "/resources", path=path, limit=PAGE_SIZE, offset=offset, fields=ITEM_FIELDS
            )
            embedded = data.get("_embedded", {})
            items = embedded.get("items", [])
            yield from items
            offset += len(items)
            if not items or offset >= embedded.get("total", 0):
                return

    def walk(self, path, recursive=True):
        root = self.stat(path)
        if root["type"] == "file":
            yield {
                "path": root["path"],
                "rel": root["name"],
                "size": root.get("size", 0),
                "md5": root.get("md5"),
            }
            return
        base = root["path"].rstrip("/")
        stack = [root["path"]]
        while stack:
            current = stack.pop()
            for item in self.listdir(current):
                if item["type"] == "dir":
                    if recursive:
                        stack.append(item["path"])
                    continue
                yield {
                    "path": item["path"],
                    "rel": item["path"][len(base):].lstrip("/"),
                    "size": item.get("size", 0),
                    "md5": item.get("md5"),
                }

    def download_url(self, path):
        return self._get("/resources/download", path=path)["href"]
