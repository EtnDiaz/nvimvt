# nvimvt

A k9s-inspired Go TUI for exploring HashiCorp Vault KV v2 secrets with minimal flicker and logical, split-pane navigation.

## Features
- Split-pane explorer: tree of folders/secrets on the left, detail + metadata on the right.
- Auth flows: Vault token, userpass, or Kubernetes service-account token (from a mounted secret file).
- Config-backed startup: loads/saves `~/.config/nvimvt/config.yaml` (or `$NVIMVT_CONFIG`).
- JSON workflows: inline create/edit/delete for KV v2 secrets, plus version metadata viewer.
- Comfortable display: mouse support, low-flicker redraws, concise status bar + help line.

## Requirements
- Go 1.21+
- Network access to download modules (`github.com/hashicorp/vault/api`, `github.com/rivo/tview`, `github.com/gdamore/tcell/v2`, `gopkg.in/yaml.v3`).

## Quick start
```bash
go mod tidy   # fetch deps (may need GOPROXY=direct in restricted networks)
go run ./...
```

## Key bindings
- `Enter`: open folder or read selected secret
- `b` / `Backspace`: go up a folder
- `r`: refresh current node/secret
- `n`: create secret (inline JSON editor)
- `e`: edit secret (writes a new version)
- `d`: delete secret (all versions)
- `v`: show version metadata
- `l`: login form (address/auth/mount)
- `s`: save config to disk
- `?`: toggle help footer
- `q`: quit

## Auth
Set fields in the login form or config file:
- **token**: provide a Vault token.
- **userpass**: username + password for `auth/userpass`.
- **k8s**: read a Kubernetes service-account token from `k8s_token_path` and login to the Kubernetes auth method using `k8s_role`.

## Config file
Default path: `~/.config/nvimvt/config.yaml` (override with `$NVIMVT_CONFIG`).

Example:
```yaml
address: http://127.0.0.1:8200
mount_point: secret
auth_method: token
token: s.xxxxxx
# username: dev
# password: example
k8s_token_path: /var/run/secrets/kubernetes.io/serviceaccount/token
k8s_role: default
```

## Notes
- The explorer assumes a KV v2 mount; adjust `mount_point` as needed.
- Secret bodies are JSON objects; invalid JSON will be rejected on create/edit.
- In restricted environments you may need to set `GOPROXY=direct` before `go mod tidy`.
