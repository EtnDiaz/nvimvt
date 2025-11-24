# nvimvt

A curses-based terminal UI for HashiCorp Vault secrets. It focuses on developers who can port-forward to Vault but cannot reach it via Tailscale.

## Features
- Login with Vault **token**, **userpass**, or **k8s-secret** (read a token from a mounted Kubernetes secret file).
- Config-driven startup that remembers your last session (address, mount, auth method, token paths) in `~/.config/nvimvt/config.yaml`.
- Full-screen TUI with low-flicker redraws, color-coded headers, and a k9s-inspired hotkey dashboard.
- Work with KV v2 secrets: list keys, read a version, create or update (new version), and delete versions or entire metadata.

## Requirements
- Python 3.10+
- `pip install -r requirements.txt` (installs `hvac==2.4.0` and `PyYAML`).
- Access to a Vault instance with KV v2 enabled on the chosen mount (defaults to `secret`).

## Run
```bash
pip install -r requirements.txt
python nvimvt.py
```

### Login screen
- Use **Tab/Shift+Tab** to move between fields and **Enter** to edit a field.
- Press **F5** to connect with the selected auth method.
- Press **F2** to save the current form to `~/.config/nvimvt/config.yaml` (created with `0600` permissions).

Supported auth methods:
- `token`: connect with the provided Vault token.
- `userpass`: connect with username/password on the configured mount.
- `k8s-secret`: use the provided token, or read one from `k8s_token_path` (defaults to `/var/run/secrets/kubernetes.io/serviceaccount/token`).

### Dashboard
Hotkeys mirror the minimal k9s-style palette:
- `L` list a path
- `R` read a secret (optionally choose a version)
- `W` create/update using multi-line `key=value` pairs
- `D` delete (soft delete or destroy all versions)
- `M` change the KV v2 mount point
- `G` reopen the login form and re-authenticate
- `Q` quit

### Config file
A sample config is provided in `config.example.yaml`:
```yaml
address: http://127.0.0.1:8200
mount_point: secret
auth_method: token
# token: s.xxxxxx
k8s_token_path: /var/run/secrets/kubernetes.io/serviceaccount/token
# username: dev
# password: example
```

Copy it to `~/.config/nvimvt/config.yaml` (or point `NVIMVT_CONFIG` to another path) to pre-seed defaults before launching. Secrets are not encrypted; store only development tokens.

## Notes
- The app assumes a KV v2 mount point; update the mount prompt to match your Vault.
- Errors from Vault are reported inline so you can adjust paths or permissions quickly.
