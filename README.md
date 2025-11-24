# nvimvt

A k9s-inspired curses UI for exploring HashiCorp Vault KV v2 secrets. Designed for developers who port-forward to Vault but need a fast terminal explorer to browse, read, version, and edit secrets.

## Features
- **Full-screen explorer**: tree navigation for KV v2 paths with live detail pane and version metadata.
- **Auth options**: token, userpass, or Kubernetes service-account token (from a mounted secret file).
- **Config-backed startup**: remembers address, mount, and auth fields in `~/.config/nvimvt/config.yaml` (or `$NVIMVT_CONFIG`).
- **Low-flicker rendering**: uses `noutrefresh`/`doupdate` and a subdued color theme for comfortable daily use.

## Requirements
- Python 3.10+
- `pip install -r requirements.txt` (installs `hvac==2.4.0` and `PyYAML`)
- Access to a Vault instance with a KV v2 mount (defaults to `secret`)

## Running
```bash
pip install -r requirements.txt
python nvimvt.py
```

## Login
- Tab/Shift+Tab to move, Enter to edit a field.
- F5 authenticates with the selected method; F2 saves the form to `~/.config/nvimvt/config.yaml` (0600 permissions).
- Auth methods:
  - `token`: direct Vault token.
  - `userpass`: username/password.
  - `k8s-secret`: read a JWT from `k8s_token_path` (defaults to the Kubernetes service account token file) and log in via the Kubernetes auth method.

## Explorer
Hotkeys:
- **Enter**: open folder / refresh selected secret
- **↑/↓** or **j/k**: move selection
- **b**: go up one folder
- **r**: reload current folder
- **n**: create a secret (prompts for name and JSON payload)
- **e**: edit selected secret (writes a new version)
- **d**: delete selected secret (metadata + all versions)
- **v**: toggle version list display for the selected secret
- **q**: quit

The left pane lists folders (ending with `/`) and secrets. The right pane shows the selected secret's JSON payload plus version metadata and, when toggled, a list of available versions.

## Config file
Example `config.example.yaml`:
```yaml
address: http://127.0.0.1:8200
mount_point: secret
auth_method: token
# token: s.xxxxxx
k8s_token_path: /var/run/secrets/kubernetes.io/serviceaccount/token
# username: dev
# password: example
```

Copy the file to `~/.config/nvimvt/config.yaml` (or point `NVIMVT_CONFIG` elsewhere) to pre-fill defaults.

## Notes
- The UI assumes KV v2; update `mount_point` if your mount differs.
- Secrets are edited as JSON objects; non-object payloads are rejected for safety.
- Kubernetes auth uses the `default` role. Adjust Vault policies/roles as needed for your cluster.
