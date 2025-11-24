from __future__ import annotations

"""
Nvimvt: curses-based Vault KV explorer with theme and config support.
"""

import curses
import curses.panel
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

CONFIG_PATH = Path(
    os.environ.get("NVIMVT_CONFIG", Path.home() / ".config" / "nvimvt" / "config.yaml")
)


@dataclass
class AppConfig:
    address: str = "http://127.0.0.1:8200"
    mount_point: str = "secret"
    auth_method: str = "token"  # token|userpass|k8s-secret
    token: str = ""
    username: str = ""
    password: str = ""
    k8s_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"

    @classmethod
    def load(cls) -> "AppConfig":
        if CONFIG_PATH.exists():
            try:
                data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
                defaults = {f: getattr(cls(), f) for f in cls.__dataclass_fields__}
                defaults.update({k: v for k, v in data.items() if k in defaults})
                return cls(**defaults)
            except Exception:  # noqa: BLE001
                return cls()
        return cls()

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(yaml.safe_dump(self.__dict__, sort_keys=False))
        os.chmod(CONFIG_PATH, 0o600)


class VaultClient:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.client: Optional[hvac.Client] = None

    def connect(self) -> None:
        self.client = hvac.Client(url=self.cfg.address)
        if not self.client.is_authenticated():
            self._login()

    def _login(self) -> None:
        if self.cfg.auth_method == "token":
            if not self.cfg.token:
                raise ValueError("Token is required for token auth")
            self.client.token = self.cfg.token
        elif self.cfg.auth_method == "userpass":
            if not (self.cfg.username and self.cfg.password):
                raise ValueError("Username/password required for userpass auth")
            self.client.auth.userpass.login(self.cfg.username, self.cfg.password)
        elif self.cfg.auth_method == "k8s-secret":
            token_path = Path(self.cfg.k8s_token_path)
            if not token_path.exists():
                raise ValueError(f"Kubernetes token not found at {token_path}")
            jwt = token_path.read_text().strip()
            self.client.auth.kubernetes.login(role="default", jwt=jwt)
        else:
            raise ValueError(f"Unsupported auth method: {self.cfg.auth_method}")
        if not self.client.is_authenticated():
            raise RuntimeError("Authentication failed")

    # KV helpers
    def list_keys(self, path: str) -> List[str]:
        api = self.client.secrets.kv.v2
        full_path = path.rstrip("/")
        try:
            resp = api.list_secrets(path=full_path, mount_point=self.cfg.mount_point)
            return sorted(resp["data"].get("keys", []))
        except hvac.exceptions.InvalidPath:
            return []

    def read_secret(self, path: str, version: Optional[int] = None) -> Dict:
        api = self.client.secrets.kv.v2
        resp = api.read_secret_version(
            path=path.rstrip("/"), version=version, mount_point=self.cfg.mount_point
        )
        return resp["data"]

    def read_metadata(self, path: str) -> Dict:
        api = self.client.secrets.kv.v2
        resp = api.read_metadata(path=path.rstrip("/"), mount_point=self.cfg.mount_point)
        return resp["data"]

    def write_secret(self, path: str, payload: Dict) -> None:
        api = self.client.secrets.kv.v2
        api.create_or_update_secret(
            path=path.rstrip("/"), secret=payload, mount_point=self.cfg.mount_point
        )

    def delete_secret(self, path: str) -> None:
        api = self.client.secrets.kv.v2
        api.delete_metadata_and_all_versions(path=path.rstrip("/"), mount_point=self.cfg.mount_point)


class Modal:
    def __init__(self, stdscr: curses.window, title: str, height: int, width: int):
        max_y, max_x = stdscr.getmaxyx()
        top = max((max_y - height) // 2, 1)
        left = max((max_x - width) // 2, 2)
        self.win = curses.newwin(height, width, top, left)
        self.win.keypad(True)
        self.win.border()
        self.win.addstr(0, 2, f" {title} ")

    def refresh(self) -> None:
        self.win.noutrefresh()


class LineInput(Modal):
    def __init__(self, stdscr: curses.window, title: str, initial: str = ""):
        super().__init__(stdscr, title, height=5, width=max(40, len(initial) + 10))
        self.initial = initial

    def capture(self) -> str:
        self.win.addstr(2, 2, self.initial)
        box = curses.textpad.Textbox(self.win.derwin(1, self.win.getmaxyx()[1] - 4, 2, 2))
        curses.curs_set(1)
        text = box.edit().strip()
        curses.curs_set(0)
        return text or self.initial


class MultiLineEditor(Modal):
    def __init__(self, stdscr: curses.window, title: str, initial: str = ""):
        super().__init__(stdscr, title, height=15, width=80)
        self.initial = initial

    def capture(self) -> str:
        lines = self.initial.splitlines()
        for idx, line in enumerate(lines[: self.win.getmaxyx()[0] - 3]):
            self.win.addstr(1 + idx, 2, line[: self.win.getmaxyx()[1] - 4])
        edit_win = self.win.derwin(self.win.getmaxyx()[0] - 2, self.win.getmaxyx()[1] - 4, 1, 2)
        editor = curses.textpad.Textbox(edit_win)
        curses.curs_set(1)
        content = editor.edit().strip()
        curses.curs_set(0)
        return content or self.initial


class TuiApp:
    def __init__(self) -> None:
        self.cfg = AppConfig.load()
        self.vault = VaultClient(self.cfg)
        self.status: List[str] = []
        self.current_path: str = ""
        self.items: List[str] = []
        self.selected: int = 0
        self.show_versions: bool = False
        self.colors_ready = False

    # Color/theme
    def init_colors(self) -> None:
        if self.colors_ready:
            return
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)  # accent
            curses.init_pair(2, curses.COLOR_MAGENTA, -1)  # header
            curses.init_pair(3, curses.COLOR_YELLOW, -1)  # highlight
            curses.init_pair(4, curses.COLOR_GREEN, -1)  # success
            curses.init_pair(5, curses.COLOR_RED, -1)  # errors
        self.colors_ready = True

    def log(self, message: str, level: str = "info") -> None:
        prefix = {
            "info": "●",
            "ok": "✓",
            "err": "!",
        }.get(level, "●")
        self.status.append(f"{prefix} {message}")
        self.status = self.status[-5:]

    # Screens
    def run(self, stdscr: curses.window) -> None:
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        self.init_colors()
        self.login_screen(stdscr)
        self.explorer(stdscr)

    def login_screen(self, stdscr: curses.window) -> None:
        fields = [
            ["Vault address", self.cfg.address, False],
            ["KV mount", self.cfg.mount_point, False],
            ["Auth (token/userpass/k8s-secret)", self.cfg.auth_method, False],
            ["Token", self.cfg.token, True],
            ["Username", self.cfg.username, False],
            ["Password", self.cfg.password, True],
            ["K8s token path", self.cfg.k8s_token_path, True],
        ]
        idx = 0
        while True:
            stdscr.erase()
            self.draw_header(stdscr, "Vault login")
            hints = "Tab/Shift+Tab navigate • Enter edit • F2 save config • F5 connect • q quit"
            stdscr.addstr(2, 2, hints, curses.color_pair(1))
            for i, (label, value, secret) in enumerate(fields):
                marker = "➤" if i == idx else " "
                display = "*" * len(value) if secret and value else (value or "<empty>")
                stdscr.addstr(4 + i, 4, f"{marker} {label:<32}: {display}")
            self.draw_status(stdscr)
            stdscr.noutrefresh()
            curses.doupdate()

            key = stdscr.getch()
            if key in (KEY_TAB, 9):
                idx = (idx + 1) % len(fields)
            elif key == KEY_BTAB:
                idx = (idx - 1) % len(fields)
            elif key in (curses.KEY_ENTER, 10, 13):
                title, current, secret = fields[idx]
                editor = LineInput(stdscr, title, current)
                new_val = editor.capture()
                fields[idx][1] = new_val
            elif key == curses.KEY_F2:
                self.cfg.address = fields[0][1].strip()
                self.cfg.mount_point = fields[1][1].strip()
                self.cfg.auth_method = fields[2][1].strip().lower() or "token"
                self.cfg.token = fields[3][1].strip()
                self.cfg.username = fields[4][1].strip()
                self.cfg.password = fields[5][1].strip()
                self.cfg.k8s_token_path = fields[6][1].strip()
                self.cfg.save()
                self.log(f"Config saved to {CONFIG_PATH}", "ok")
            elif key == curses.KEY_F5:
                self.cfg.address = fields[0][1].strip()
                self.cfg.mount_point = fields[1][1].strip()
                self.cfg.auth_method = fields[2][1].strip().lower() or "token"
                self.cfg.token = fields[3][1].strip()
                self.cfg.username = fields[4][1].strip()
                self.cfg.password = fields[5][1].strip()
                self.cfg.k8s_token_path = fields[6][1].strip()
                try:
                    self.vault = VaultClient(self.cfg)
                    self.vault.connect()
                    self.log("Authenticated", "ok")
                    self.refresh_listing()
                    return
                except Exception as exc:  # noqa: BLE001
                    self.log(str(exc), "err")
            elif key in (ord("q"), 27):
                raise KeyboardInterrupt

    def explorer(self, stdscr: curses.window) -> None:
        while True:
            stdscr.erase()
            self.draw_header(stdscr, f"Mount: {self.cfg.mount_point} :: {self.current_path or '/'}")
            max_y, max_x = stdscr.getmaxyx()
            sidebar_w = max(32, max_x // 3)
            body_w = max_x - sidebar_w - 1
            body_h = max_y - 5

            sidebar = curses.newwin(body_h, sidebar_w, 2, 0)
            details = curses.newwin(body_h, body_w, 2, sidebar_w + 1)
            sidebar.keypad(True)

            self.draw_sidebar(sidebar)
            self.draw_details(details)
            self.draw_status(stdscr)

            sidebar.noutrefresh()
            details.noutrefresh()
            stdscr.noutrefresh()
            curses.doupdate()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                self.selected = max(0, self.selected - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.selected = min(len(self.items) - 1, self.selected + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                self.enter_item()
            elif key == ord("b"):
                self.go_up()
            elif key == ord("r"):
                self.refresh_listing()
            elif key == ord("n"):
                self.create_secret(stdscr)
            elif key == ord("e"):
                self.edit_secret(stdscr)
            elif key == ord("d"):
                self.delete_secret(stdscr)
            elif key == ord("v"):
                self.show_versions = not self.show_versions
            elif key in (ord("q"), 27):
                return

    # Drawing
    def draw_header(self, stdscr: curses.window, title: str) -> None:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.addstr(0, 2, f"nvimvt :: {title}", curses.color_pair(2) | curses.A_BOLD)
        stdscr.hline(1, 0, curses.ACS_HLINE, max_x)

    def draw_sidebar(self, win: curses.window) -> None:
        win.erase()
        win.border()
        win.addstr(0, 2, " Paths (Enter/open, b/up, n/new, d/delete) ", curses.color_pair(1))
        for idx, item in enumerate(self.items):
            marker = "➤" if idx == self.selected else " "
            display = item
            if display.endswith("/"):
                display = f"{display}"
            style = curses.A_BOLD if display.endswith("/") else curses.A_NORMAL
            if idx == self.selected:
                style |= curses.color_pair(3)
            win.addstr(1 + idx, 2, f"{marker} {display}", style)

    def draw_details(self, win: curses.window) -> None:
        win.erase()
        win.border()
        win.addstr(0, 2, " Secret details (e edit, v versions) ", curses.color_pair(1))
        if not self.items:
            win.addstr(2, 2, "No items. Press n to create a secret.")
            return
        current = self.items[self.selected]
        if current.endswith("/"):
            win.addstr(2, 2, "Folder. Enter to open.")
            return
        try:
            secret = self.vault.read_secret(self.current_path + current)
            data = secret.get("data", {})
            meta = secret.get("metadata", {})
            lines = json.dumps(data, indent=2).splitlines()
            for idx, line in enumerate(lines[: win.getmaxyx()[0] - 4]):
                win.addstr(2 + idx, 2, line[: win.getmaxyx()[1] - 4])
            win.addstr(win.getmaxyx()[0] - 3, 2, f"Version: {meta.get('version')} • Created: {meta.get('created_time')}")
            if self.show_versions:
                meta_info = self.vault.read_metadata(self.current_path + current)
                win.addstr(win.getmaxyx()[0] - 2, 2, f"Versions: {sorted(meta_info.get('versions', {}).keys())}")
        except Exception as exc:  # noqa: BLE001
            win.addstr(2, 2, f"Error: {exc}", curses.color_pair(5))

    def draw_status(self, stdscr: curses.window) -> None:
        max_y, max_x = stdscr.getmaxyx()
        y = max_y - 3
        stdscr.hline(y, 0, curses.ACS_HLINE, max_x)
        stdscr.addstr(y, 2, " Status (r reload, e edit, n new, d delete, v versions, q quit) ", curses.color_pair(1))
        for idx, line in enumerate(reversed(self.status)):
            stdscr.addstr(y + 1 + idx, 2, line[: max_x - 4])

    # Actions
    def refresh_listing(self) -> None:
        self.items = self.vault.list_keys(self.current_path)
        self.selected = 0

    def enter_item(self) -> None:
        if not self.items:
            return
        item = self.items[self.selected]
        if item.endswith("/"):
            self.current_path += item
            self.refresh_listing()
        else:
            # open detail - already showing
            pass

    def go_up(self) -> None:
        if not self.current_path:
            return
        parts = self.current_path.rstrip("/").split("/")[:-1]
        self.current_path = "/".join(parts)
        if self.current_path:
            self.current_path += "/"
        self.refresh_listing()

    def create_secret(self, stdscr: curses.window) -> None:
        name_input = LineInput(stdscr, "New secret name", "")
        key = name_input.capture().strip()
        if not key:
            return
        if not key.endswith("/"):
            payload_editor = MultiLineEditor(stdscr, "Secret JSON", "{\n  \"key\": \"value\"\n}")
            raw = payload_editor.capture()
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Secret must be a JSON object")
                self.vault.write_secret(self.current_path + key, data)
                self.log(f"Wrote {self.current_path + key}", "ok")
                self.refresh_listing()
            except Exception as exc:  # noqa: BLE001
                self.log(f"Write failed: {exc}", "err")

    def edit_secret(self, stdscr: curses.window) -> None:
        if not self.items:
            return
        item = self.items[self.selected]
        if item.endswith("/"):
            return
        full_path = self.current_path + item
        try:
            secret = self.vault.read_secret(full_path)
            current_data = secret.get("data", {})
        except Exception as exc:  # noqa: BLE001
            self.log(str(exc), "err")
            return
        editor = MultiLineEditor(stdscr, f"Edit {item}", json.dumps(current_data, indent=2))
        raw = editor.capture()
        try:
            new_data = json.loads(raw)
            if not isinstance(new_data, dict):
                raise ValueError("Secret must be a JSON object")
            self.vault.write_secret(full_path, new_data)
            self.log(f"Updated {full_path}", "ok")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Update failed: {exc}", "err")

    def delete_secret(self, stdscr: curses.window) -> None:
        if not self.items:
            return
        item = self.items[self.selected]
        if item.endswith("/"):
            return
        confirm = LineInput(stdscr, "Type DELETE to confirm", "")
        text = confirm.capture()
        if text.strip().upper() != "DELETE":
            return
        try:
            self.vault.delete_secret(self.current_path + item)
            self.log(f"Deleted {self.current_path + item}", "ok")
            self.refresh_listing()
        except Exception as exc:  # noqa: BLE001
            self.log(f"Delete failed: {exc}", "err")


def main() -> None:
    app = TuiApp()
    curses.wrapper(app.run)


if __name__ == "__main__":
    main()
