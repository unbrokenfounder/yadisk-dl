import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.filesize import decimal
from rich.markup import escape
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__
from .api import API_URL, Disk, DiskError, normalize
from .transfer import run

TOKEN_URL = "https://yandex.ru/dev/disk/poligon/"
MAX_LISTED = 40


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="yadisk-dl",
        description="Скачивание папок и файлов с Яндекс Диска через API.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="путь на Диске, например /Фото/2026; если не указан, программа спросит",
    )
    parser.add_argument("-o", "--out", help="локальная папка для сохранения")
    parser.add_argument(
        "-j", "--workers", type=int, default=6, help="число параллельных загрузок (по умолчанию 6)"
    )
    parser.add_argument(
        "--no-subfolders", action="store_true", help="не заходить во вложенные папки"
    )
    parser.add_argument("-y", "--yes", action="store_true", help="не просить подтверждения")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def ask_token(console, interactive):
    token = os.environ.get("YADISK_TOKEN", "").strip()
    if token:
        return token
    if not interactive:
        raise DiskError("Задайте токен в переменной окружения YADISK_TOKEN.")
    console.print(f"Нужен OAuth-токен Яндекс Диска. Получить его можно здесь: {TOKEN_URL}")
    token = Prompt.ask("Токен (ввод скрыт)", password=True, console=console).strip()
    if not token:
        raise DiskError("Токен не введён.")
    return token


def choose_path(disk, console):
    entries = sorted(
        disk.listdir("disk:/"), key=lambda item: (item["type"] != "dir", item["name"].lower())
    )
    table = Table(box=None, show_header=False, padding=(0, 2))
    for number, item in enumerate(entries[:MAX_LISTED], 1):
        suffix = "/" if item["type"] == "dir" else ""
        table.add_row(str(number), escape(item["name"] + suffix))
    console.print(table)
    if len(entries) > MAX_LISTED:
        console.print(f"... и ещё {len(entries) - MAX_LISTED}. Остальное можно ввести путём.")
    answer = Prompt.ask(
        "Что скачать? Номер из списка или путь на Диске (Enter: весь Диск)",
        default="",
        show_default=False,
        console=console,
    ).strip()
    if not answer:
        return "disk:/"
    if answer.isdigit() and 1 <= int(answer) <= min(len(entries), MAX_LISTED):
        return entries[int(answer) - 1]["path"]
    return normalize(answer)


def choose_out(path, console):
    name = path.rstrip("/").rsplit("/", 1)[-1].replace("disk:", "") or "yadisk"
    default = Path.home() / "Downloads" / name
    answer = Prompt.ask("Куда сохранить", default=str(default), console=console)
    return Path(answer).expanduser()


def scan(disk, path, recursive, console):
    files = []
    with console.status("Читаю список файлов...") as status:
        for item in disk.walk(path, recursive):
            files.append(item)
            if len(files) % 25 == 0:
                status.update(f"Читаю список файлов: {len(files)}")
    return files


def fmt_time(seconds):
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def show_summary(console, path, out, files):
    table = Table.grid(padding=(0, 2))
    table.add_row("Источник", escape(path))
    table.add_row("Файлов", str(len(files)))
    table.add_row("Объём", decimal(sum(f["size"] for f in files)))
    table.add_row("Папка", escape(str(out)))
    console.print(table)


def report(console, result):
    if result.interrupted:
        console.print(
            "Остановлено. Запустите ту же команду снова: готовые файлы будут пропущены."
        )
    speed = int(result.bytes / result.elapsed) if result.elapsed > 0 else 0
    console.print(
        f"Время {fmt_time(result.elapsed)}. Скачано файлов: {result.downloaded} "
        f"({decimal(result.bytes)}), пропущено: {result.skipped}, "
        f"средняя скорость {decimal(speed)}/с."
    )
    for name, message in result.failed[:20]:
        console.print(f"[red]Не скачан:[/] {escape(name)} ({escape(message)})")
    if len(result.failed) > 20:
        console.print(f"... и ещё {len(result.failed) - 20}")


def main(argv=None):
    args = parse_args(argv)
    console = Console()
    interactive = sys.stdin.isatty()
    if not interactive and not (args.path and args.out):
        console.print("[red]Без терминала нужно указать путь на Диске и параметр --out.")
        return 2
    workers = max(1, min(args.workers, 16))
    try:
        disk = Disk(ask_token(console, interactive), os.environ.get("YADISK_API_URL", API_URL))
        login = (disk.check().get("user") or {}).get("login")
        if login:
            console.print(f"Аккаунт: [bold]{escape(login)}[/]")
        path = normalize(args.path) if args.path else choose_path(disk, console)
        out = Path(args.out).expanduser() if args.out else choose_out(path, console)
        files = scan(disk, path, not args.no_subfolders, console)
        if not files:
            console.print("В выбранном месте нет файлов.")
            return 0
        show_summary(console, path, out, files)
        if interactive and not args.yes:
            if not Confirm.ask("Начать скачивание?", default=True, console=console):
                return 0
        result = run(disk, files, out, workers, console)
    except DiskError as exc:
        console.print(f"[red]{escape(str(exc))}")
        return 1
    except KeyboardInterrupt:
        console.print("\nОстановлено.")
        return 130
    report(console, result)
    if result.interrupted:
        return 130
    return 1 if result.failed else 0
