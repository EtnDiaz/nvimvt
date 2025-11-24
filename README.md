# nvimvt

A curses-based terminal UI for HashiCorp Vault secrets. It focuses on developers who can port-forward to Vault but cannot reach it via TailScale.

## Features
- Login with a Vault token or username/password against a chosen Vault address.
- Work with KV v2 secrets: list keys, read a version, create or update (new version), and delete versions or entire metadata.
- Full-screen TUI with a login form and hotkeys for each secret action.

## Requirements
- Python 3.10+
- `hvac` library pinned to 2.4.0 (install with `pip install -r requirements.txt`).
- Access to a Vault instance with KV v2 enabled on the chosen mount (defaults to `secret`).

## Usage
1. Install dependencies: `pip install -r requirements.txt`.
2. Run the TUI: `python nvimvt.py`.
3. Use **Tab/Shift+Tab** and **Enter** on the login form to edit fields; press **F5** to connect.
4. In the dashboard press hotkeys to manage secrets:
   - `L` list a path
   - `R` read a secret (optionally choose a version)
   - `W` create/update using multi-line `key=value` pairs
   - `D` delete (soft delete or destroy all versions)
   - `M` change the KV v2 mount point
   - `G` reopen the login form and re-authenticate
   - `Q` quit

### Secret operations
- **List**: shows keys and folders for a relative path under the mount.
- **Read**: displays secret data and metadata; supports reading a specific version.
- **Create/Update**: enter `key=value` pairs to write a new version.
- **Delete**: either mark the latest version as deleted or remove all versions and metadata.

## Notes
- The app assumes a KV v2 mount point; update the mount prompt to match your Vault.
- Errors from Vault are reported inline so you can adjust paths or permissions quickly.
