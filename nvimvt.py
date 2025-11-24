"""
Curses-based TUI for managing HashiCorp Vault KV v2 secrets.
"""
from __future__ import annotations

import curses
import curses.textpad
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import hvac
import yaml

KEY_TAB = getattr(curses, "KEY_TAB", 9)
KEY_BTAB = getattr(curses, "KEY_BTAB", 353)


CONFIG_PATH = Path(os.environ.get("NVIMVT_CONFIG", Path.home() / ".config" / "nvimvt" / "config.yaml"))


@dataclass
class FormField:
    label: str
    value: str = ""
    secret: bool = False

    def display_value(self) -> str:
        if self.secret and self.value:
            return "*" * len(self.value)
        return self.value or "<empty>"


@dataclass
class AppConfig:
    address: str = "http://127.0.0.1:8200"
    mount_point: str = "secret"
    auth_method: str = "token"
    token: str = ""
    username: str = ""
    password: str = ""
    k8s_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"

    @classmethod
    def load(cls) -> "AppConfig":
        if CONFIG_PATH.exists():
            try:
                data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
                defaults = {field: getattr(cls(), field) for field in cls.__dataclass_fields__}
                defaults.update(data)
                return cls(**defaults)
            except Exception:  # noqa: BLE001
                pass
        return cls()

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(yaml.safe_dump(self.__dict__, sort_keys=False))
        os.chmod(CONFIG_PATH, 0o600)


class TextInput:
    def __init__(self, stdscr: curses.window, title: str, initial: str = "", height: int = 3):
        self.stdscr = stdscr
        self.title = title
        self.initial = initial
        self.height = height

    def capture(self) -> str:
        max_y, max_x = self.stdscr.getmaxyx()
        width = max_x - 4
        start_y = max_y // 2 - self.height // 2
        start_x = 2
        win = curses.newwin(self.height, width, start_y, start_x)
        win.border()
        win.addstr(0, 2, f" {self.title} ")
        win.addstr(1, 2, self.initial)
        curses.curs_set(1)
        textbox = curses.textpad.Textbox(win.derwin(1, width - 4, 1, 2))
        textbox.stripspaces = True
        result = textbox.edit().strip()
        curses.curs_set(0)
        return result or self.initial


class MultiLineInput:
    def __init__(self, stdscr: curses.window, title: str, initial: str = "", height: int = 10):
        self.stdscr = stdscr
        self.title = title
        self.initial = initial
        self.height = height

    def capture(self) -> str:
        max_y, max_x = self.stdscr.getmaxyx()
        width = max_x - 4
        start_y = max_y // 2 - self.height // 2
        start_x = 2
        win = curses.newwin(self.height, width, start_y, start_x)
        win.border()
        win.addstr(0, 2, f" {self.title} (Ctrl+G to submit) ")
        for idx, line in enumerate(self.initial.splitlines()):
            win.addstr(1 + idx, 2, line)
        curses.curs_set(1)
        textbox = curses.textpad.Textbox(win.derwin(self.height - 2, width - 4, 1, 2))
        content = textbox.edit().strip()
        curses.curs_set(0)
        return content or self.initial


class NvimvtApp:
    def __init__(self) -> None:
        self.config = AppConfig.load()
        self.client: Optional[hvac.Client] = None
        self.address = self.config.address
        self.mount_point = self.config.mount_point
        self.fields: List[FormField] = [
            FormField("Vault address", self.address),
            FormField("KV v2 mount", self.mount_point),
            FormField("Auth: token/userpass/k8s-secret", self.config.auth_method),
            FormField("Token", self.config.token, secret=True),
            FormField("K8s token path", self.config.k8s_token_path, secret=True),
            FormField("Username", self.config.username),
            FormField("Password", self.config.password, secret=True),
        ]
        self.status_lines: List[str] = []
        self.theme_ready = False

    # UI helpers
    def log(self, message: str) -> None:
        self.status_lines.append(message)
        self.status_lines = self.status_lines[-8:]

    def init_colors(self) -> None:
        if self.theme_ready:
            return
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)  # accents
            curses.init_pair(2, curses.COLOR_YELLOW, -1)  # headers
            curses.init_pair(3, curses.COLOR_GREEN, -1)  # success
            curses.init_pair(4, curses.COLOR_RED, -1)  # errors
        self.theme_ready = True

    def draw_header(self, stdscr: curses.window, title: str) -> None:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.addstr(0, 2, f"nvimvt :: {title}", curses.color_pair(2) | curses.A_BOLD)
        stdscr.hline(1, 0, curses.ACS_HLINE, max_x)

    def draw_status(self, stdscr: curses.window) -> None:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.hline(max_y - 9, 0, curses.ACS_HLINE, max_x)
        stdscr.addstr(max_y - 9, 2, " Recent events ", curses.color_pair(1) | curses.A_BOLD)
        for idx, line in enumerate(self.status_lines[-8:]):
            stdscr.addstr(max_y - 8 + idx, 2, line[: max_x - 4])

    # Login and configuration
    def login_screen(self, stdscr: curses.window) -> None:
        current = 0
        while True:
            stdscr.erase()
            self.draw_header(stdscr, "Vault configuration")
            stdscr.addstr(2, 2, "Tab: next field • Enter: edit • F2: save cfg • F5: connect")
            for idx, field in enumerate(self.fields):
                prefix = "→ " if idx == current else "  "
                stdscr.addstr(4 + idx, 2, f"{prefix}{field.label}: {field.display_value()}")
            self.draw_status(stdscr)
            stdscr.noutrefresh()
            curses.doupdate()

            key = stdscr.getch()
            if key in (KEY_TAB, 9):
                current = (current + 1) % len(self.fields)
            elif key in (KEY_BTAB,):  # Shift+Tab
                current = (current - 1) % len(self.fields)
            elif key in (curses.KEY_ENTER, 10, 13):
                selected = self.fields[current]
                editor = TextInput(
                    stdscr,
                    selected.label,
                    initial=selected.value,
                    height=3,
                )
                selected.value = editor.capture()
                if selected.label == "Auth: token/userpass/k8s-secret":
                    selected.value = selected.value.lower().strip() or "token"
            elif key == curses.KEY_F2:
                self.persist_form(save=True)
            elif key == curses.KEY_F5:
                if self.try_authenticate():
                    return
            elif key in (ord("q"), 27):
                raise KeyboardInterrupt

    def try_authenticate(self) -> bool:
        address = self.fields[0].value or self.address
        mount = self.fields[1].value or self.mount_point
        method = self.fields[2].value.lower() or "token"
        token = self.fields[3].value
        k8s_token_path = self.fields[4].value or self.config.k8s_token_path
        username = self.fields[5].value
        password = self.fields[6].value

        client = hvac.Client(url=address)
        try:
            if method == "token":
                if not token:
                    self.log("Token required for token auth")
                    return False
                client.token = token
            elif method == "k8s-secret":
                path = Path(k8s_token_path)
                if not token:
                    if not path.exists():
                        self.log(f"No token at {path}")
                        return False
                    token = path.read_text().strip()
                client.token = token
            elif method == "userpass":
                client.auth.userpass.login(username=username, password=password)
            else:
                self.log("Auth method must be token, userpass, or k8s-secret")
                return False
            if client.is_authenticated():
                self.client = client
                self.address = address
                self.mount_point = mount
                self.persist_form(save=True)
                self.log(f"Connected to {address} (mount: {mount})")
                return True
            self.log("Authentication failed")
            return False
        except Exception as exc:  # noqa: BLE001
            self.log(f"Auth error: {exc}")
            return False

    def persist_form(self, save: bool = False) -> None:
        self.config.address = self.fields[0].value or self.address
        self.config.mount_point = self.fields[1].value or self.mount_point
        self.config.auth_method = self.fields[2].value or "token"
        self.config.token = self.fields[3].value
        self.config.k8s_token_path = self.fields[4].value or self.config.k8s_token_path
        self.config.username = self.fields[5].value
        self.config.password = self.fields[6].value
        if save:
            self.config.save()
            self.log(f"Saved config to {CONFIG_PATH}")

    # Main dashboard
    def dashboard(self, stdscr: curses.window) -> None:
        while True:
            stdscr.erase()
            self.draw_header(stdscr, f"Vault: {self.address} • mount: {self.mount_point}")
            stdscr.addstr(
                2,
                2,
                "[L]ist [R]ead [W]rite [D]elete [M]ount change [G]auth [Q]uit",
                curses.color_pair(1) | curses.A_BOLD,
            )
            self.draw_status(stdscr)
            stdscr.noutrefresh()
            curses.doupdate()

            key = stdscr.getch()
            if key in (ord("l"), ord("L")):
                self.list_secrets(stdscr)
            elif key in (ord("r"), ord("R")):
                self.read_secret(stdscr)
            elif key in (ord("w"), ord("W")):
                self.write_secret(stdscr)
            elif key in (ord("d"), ord("D")):
                self.delete_secret(stdscr)
            elif key in (ord("m"), ord("M")):
                self.change_mount(stdscr)
            elif key in (ord("g"), ord("G")):
                self.login_screen(stdscr)
            elif key in (ord("q"), ord("Q")):
                break

    # Operations
    def list_secrets(self, stdscr: curses.window) -> None:
        path = TextInput(stdscr, "List path (relative)", "").capture()
        try:
            response = self.client.secrets.kv.v2.list_secrets(
                path=path, mount_point=self.mount_point
            )
            keys = response["data"].get("keys", [])
            if keys:
                listing = ", ".join(keys)
                self.log(f"Contents of {path or '/'}: {listing}")
            else:
                self.log(f"No secrets under {path or '/'}")
        except hvac.exceptions.InvalidPath:
            self.log("Invalid path for listing")
        except Exception as exc:  # noqa: BLE001
            self.log(f"List error: {exc}")

    def read_secret(self, stdscr: curses.window) -> None:
        path = TextInput(stdscr, "Read path", "").capture()
        version_text = TextInput(stdscr, "Version (blank = latest)", "").capture()
        version = int(version_text) if version_text else None
        try:
            response = self.client.secrets.kv.v2.read_secret_version(
                path=path, version=version, mount_point=self.mount_point
            )
            data = response["data"]["data"]
            metadata = response["data"].get("metadata", {})
            formatted = json.dumps({"data": data, "metadata": metadata}, indent=2)
            self.display_output(stdscr, "Secret", formatted)
            self.log(f"Read {path}")
        except hvac.exceptions.InvalidPath:
            self.log("Secret not found")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Read error: {exc}")

    def write_secret(self, stdscr: curses.window) -> None:
        path = TextInput(stdscr, "Write path", "").capture()
        template = ""
        editor = MultiLineInput(
            stdscr,
            "key=value per line (Ctrl+G to save)",
            initial=template,
            height=8,
        )
        raw = editor.capture()
        data: Dict[str, str] = {}
        for line in raw.splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                self.log(f"Skipping line without '=': {line}")
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
        if not data:
            self.log("No data provided; aborting write")
            return
        try:
            response = self.client.secrets.kv.v2.create_or_update_secret(
                path=path, secret=data, mount_point=self.mount_point
            )
            version = response.get("data", {}).get("version")
            self.log(f"Stored {path} (version {version})")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Write error: {exc}")

    def delete_secret(self, stdscr: curses.window) -> None:
        path = TextInput(stdscr, "Delete path", "").capture()
        choice = TextInput(stdscr, "Type 'soft' or 'destroy'", "soft").capture()
        try:
            if choice.lower().startswith("soft"):
                self.client.secrets.kv.v2.delete_latest_version_of_secret(
                    path=path, mount_point=self.mount_point
                )
                self.log(f"Soft-deleted latest version of {path}")
            elif choice.lower().startswith("destroy"):
                self.client.secrets.kv.v2.delete_metadata_and_all_versions(
                    path=path, mount_point=self.mount_point
                )
                self.log(f"Destroyed all versions of {path}")
            else:
                self.log("Delete cancelled (type soft/destroy)")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Delete error: {exc}")

    def change_mount(self, stdscr: curses.window) -> None:
        mount = TextInput(stdscr, "New KV v2 mount", self.mount_point).capture()
        if mount:
            self.mount_point = mount
            self.log(f"Switched mount to {mount}")

    def display_output(self, stdscr: curses.window, title: str, body: str) -> None:
        max_y, max_x = stdscr.getmaxyx()
        height = min(max_y - 4, max(6, body.count("\n") + 4))
        width = max_x - 4
        start_y = 2
        start_x = 2
        win = curses.newwin(height, width, start_y, start_x)
        win.border()
        win.addstr(0, 2, f" {title} ")
        for idx, line in enumerate(body.splitlines()):
            if idx >= height - 2:
                break
            win.addstr(1 + idx, 2, line[: width - 4])
        win.addstr(height - 2, 2, "Press any key to continue")
        win.refresh()
        win.getch()

    def run(self, stdscr: curses.window) -> None:
        self.init_colors()
        curses.curs_set(0)
        stdscr.nodelay(False)
        if curses.has_colors():
            stdscr.bkgd(" ", curses.color_pair(0))
        try:
            self.login_screen(stdscr)
            self.dashboard(stdscr)
        except KeyboardInterrupt:
            self.log("Exiting...")


def main() -> None:
    app = NvimvtApp()
    curses.wrapper(app.run)


if __name__ == "__main__":
    main()
