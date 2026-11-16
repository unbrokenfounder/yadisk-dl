import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Column

from .api import DiskError

CHUNK = 1 << 16
ATTEMPTS = 4


class Stopped(Exception):
    pass


@dataclass
class Result:
    downloaded: int = 0
    skipped: int = 0
    bytes: int = 0
    elapsed: float = 0.0
    failed: list = field(default_factory=list)
    interrupted: bool = False


def make_progress(console):
    name = Column(min_width=24, max_width=34, no_wrap=True, overflow="ellipsis")
    return Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("{task.description}", markup=False, table_column=name),
        BarColumn(bar_width=None, complete_style="cyan", finished_style="green"),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        refresh_per_second=20,
        expand=True,
    )


def run(disk, files, out_dir, workers, console):
    out_root = out_dir.resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    total_files = len(files)
    stop = threading.Event()
    lock = threading.Lock()
    result = Result()
    progress = make_progress(console)
    overall = progress.add_task(f"Всего 0/{total_files}", total=sum(f["size"] for f in files))

    def finished(skipped, moved):
        with lock:
            if skipped:
                result.skipped += 1
            else:
                result.downloaded += 1
                result.bytes += moved
            count = result.skipped + result.downloaded
        progress.update(overall, description=f"Всего {count}/{total_files}")

    def fetch(item):
        dest = (out_root / item["rel"]).resolve()
        if out_root not in dest.parents:
            raise DiskError("недопустимое имя файла")
        size = item["size"]
        if dest.is_file() and dest.stat().st_size == size:
            progress.advance(overall, size)
            finished(True, 0)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        error = None
        try:
            for attempt in range(ATTEMPTS):
                if stop.is_set():
                    raise Stopped
                got = 0
                digest = hashlib.md5()
                try:
                    href = disk.download_url(item["path"])
                    with requests.get(href, stream=True, timeout=60) as resp:
                        resp.raise_for_status()
                        with open(part, "wb") as handle:
                            for chunk in resp.iter_content(CHUNK):
                                if stop.is_set():
                                    raise Stopped
                                handle.write(chunk)
                                digest.update(chunk)
                                got += len(chunk)
                                progress.advance(overall, len(chunk))
                    if got != size:
                        raise OSError(f"получено {got} байт из {size}")
                    expected = item.get("md5")
                    if expected and digest.hexdigest() != expected:
                        raise OSError("контрольная сумма не совпала")
                    os.replace(part, dest)
                    finished(False, got)
                    return
                except Stopped:
                    raise
                except (requests.RequestException, OSError, DiskError) as exc:
                    error = exc
                    progress.advance(overall, -got)
                    if attempt + 1 < ATTEMPTS:
                        time.sleep(2 ** attempt)
            raise DiskError(str(error))
        finally:
            part.unlink(missing_ok=True)

    start = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=workers)
    with progress:
        futures = {pool.submit(fetch, item): item for item in files}
        try:
            for future in as_completed(futures):
                try:
                    future.result()
                except Stopped:
                    pass
                except Exception as exc:
                    result.failed.append((futures[future]["rel"], str(exc)))
        except KeyboardInterrupt:
            stop.set()
            result.interrupted = True
            pool.shutdown(wait=True, cancel_futures=True)
        else:
            pool.shutdown()
    result.elapsed = time.monotonic() - start
    return result
