"""
Curses-based TUI for managing HashiCorp Vault KV v2 secrets.
"""
from __future__ import annotations

import curses
import curses.textpad
import json
from dataclasses import dataclass
from typing import Dict, List, Optional

import hvac


@dataclass
class FormField:
    label: str
    value: str = ""
    secret: bool = False

    def display_value(self) -> str:
        if self.secret and self.value:
            return "*" * len(self.value)
        return self.value or "<empty>"


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
        self.client: Optional[hvac.Client] = None
        self.address = "http://127.0.0.1:8200"
        self.mount_point = "secret"
        self.fields: List[FormField] = [
            FormField("Vault address", self.address),
            FormField("KV v2 mount", self.mount_point),
            FormField("Auth: token/userpass", "token"),
            FormField("Token", secret=True),
            FormField("Username"),
            FormField("Password", secret=True),
        ]
        self.status_lines: List[str] = []

    # UI helpers
    def log(self, message: str) -> None:
        self.status_lines.append(message)
        self.status_lines = self.status_lines[-8:]

    def draw_header(self, stdscr: curses.window, title: str) -> None:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()
        stdscr.addstr(0, 2, f"nvimvt :: {title}")
        stdscr.hline(1, 0, curses.ACS_HLINE, max_x)

    def draw_status(self, stdscr: curses.window) -> None:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.hline(max_y - 9, 0, curses.ACS_HLINE, max_x)
        stdscr.addstr(max_y - 9, 2, " Recent events ")
        for idx, line in enumerate(self.status_lines[-8:]):
            stdscr.addstr(max_y - 8 + idx, 2, line[: max_x - 4])

    # Login and configuration
    def login_screen(self, stdscr: curses.window) -> None:
        current = 0
        while True:
            self.draw_header(stdscr, "Vault configuration")
            stdscr.addstr(2, 2, "Tab: next field • Enter: edit • F5: connect")
            for idx, field in enumerate(self.fields):
                prefix = "→ " if idx == current else "  "
                stdscr.addstr(4 + idx, 2, f"{prefix}{field.label}: {field.display_value()}")
            self.draw_status(stdscr)
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_TAB, 9):
                current = (current + 1) % len(self.fields)
            elif key in (curses.KEY_BTAB, ):  # Shift+Tab
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
                if selected.label == "Auth: token/userpass":
                    selected.value = selected.value.lower().strip() or "token"
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
        username = self.fields[4].value
        password = self.fields[5].value

        client = hvac.Client(url=address)
        try:
            if method == "token":
                if not token:
                    self.log("Token required for token auth")
                    return False
                client.token = token
            elif method == "userpass":
                client.auth.userpass.login(username=username, password=password)
            else:
                self.log("Auth method must be token or userpass")
                return False
            if client.is_authenticated():
                self.client = client
                self.address = address
                self.mount_point = mount
                self.log(f"Connected to {address} (mount: {mount})")
                return True
            self.log("Authentication failed")
            return False
        except Exception as exc:  # noqa: BLE001
            self.log(f"Auth error: {exc}")
            return False

    # Main dashboard
    def dashboard(self, stdscr: curses.window) -> None:
        while True:
            self.draw_header(stdscr, f"Vault: {self.address} • mount: {self.mount_point}")
            stdscr.addstr(
                2,
                2,
                "[L]ist [R]ead [W]rite [D]elete [M]ount change [G]auth [Q]uit",
            )
            self.draw_status(stdscr)
            stdscr.refresh()

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
        curses.curs_set(0)
        stdscr.nodelay(False)
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
