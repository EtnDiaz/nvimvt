"""
Simple terminal UI for interacting with HashiCorp Vault secret engines.
"""
import getpass
import json
from typing import Dict, Optional

import hvac


def prompt(text: str, default: Optional[str] = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    full = f"{text}{suffix}: "
    if secret:
        value = getpass.getpass(full)
    else:
        value = input(full)
    if not value and default is not None:
        return default
    return value


def prompt_vault_config() -> Dict[str, str]:
    print("=== nvimvt setup ===")
    address = prompt("Vault address", default="http://127.0.0.1:8200")
    mount_point = prompt("KV v2 mount point", default="secret")
    return {"address": address, "mount_point": mount_point}


def authenticate(config: Dict[str, str]) -> hvac.Client:
    client = hvac.Client(url=config["address"])
    while True:
        print("\nSelect auth method:\n1) Token\n2) Username/Password")
        method = input("Enter choice (1/2): ").strip()
        if method == "1":
            token = prompt("Vault token", secret=True)
            client.token = token
        elif method == "2":
            username = prompt("Username")
            password = prompt("Password", secret=True)
            client.auth.userpass.login(username=username, password=password)
        else:
            print("Unknown choice. Please select 1 or 2.")
            continue
        if client.is_authenticated():
            print("\nAuthenticated successfully.\n")
            return client
        print("Authentication failed. Try again.\n")


def list_secrets(client: hvac.Client, mount_point: str) -> None:
    path = prompt("List path (relative)", default="")
    try:
        response = client.secrets.kv.v2.list_secrets(
            path=path, mount_point=mount_point
        )
        keys = response["data"].get("keys", [])
        if keys:
            print("\nSecrets and folders:")
            for key in keys:
                print(f"- {key}")
        else:
            print("\nNo secrets found at this path.")
    except hvac.exceptions.InvalidPath:
        print("Invalid path. Ensure the path exists and uses KV v2.")
    except Exception as exc:  # noqa: BLE001
        print(f"Error listing secrets: {exc}")


def read_secret(client: hvac.Client, mount_point: str) -> None:
    path = prompt("Secret path (relative)")
    version_text = prompt("Version (leave blank for latest)", default="")
    version = int(version_text) if version_text else None
    try:
        response = client.secrets.kv.v2.read_secret_version(
            path=path, version=version, mount_point=mount_point
        )
        data = response["data"]["data"]
        metadata = response["data"].get("metadata", {})
        print("\nSecret data:")
        print(json.dumps(data, indent=2))
        if metadata:
            print("\nMetadata:")
            print(json.dumps(metadata, indent=2))
    except hvac.exceptions.InvalidPath:
        print("Secret not found at the provided path.")
    except Exception as exc:  # noqa: BLE001
        print(f"Error reading secret: {exc}")


def prompt_secret_data() -> Dict[str, str]:
    print("Enter key=value pairs for the secret. Submit an empty line to finish.")
    data: Dict[str, str] = {}
    while True:
        line = input("key=value > ").strip()
        if not line:
            break
        if "=" not in line:
            print("Please use key=value format.")
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_secret(client: hvac.Client, mount_point: str) -> None:
    path = prompt("Secret path (relative)")
    data = prompt_secret_data()
    if not data:
        print("No data entered, skipping write.")
        return
    try:
        response = client.secrets.kv.v2.create_or_update_secret(
            path=path, secret=data, mount_point=mount_point
        )
        version = response.get("data", {}).get("version")
        print(f"Secret stored at {path}. New version: {version}")
    except Exception as exc:  # noqa: BLE001
        print(f"Error writing secret: {exc}")


def delete_secret(client: hvac.Client, mount_point: str) -> None:
    print("Delete options:\n1) Delete latest version (soft delete)\n2) Delete all versions and metadata")
    choice = input("Enter choice (1/2): ").strip()
    path = prompt("Secret path (relative)")
    try:
        if choice == "1":
            client.secrets.kv.v2.delete_latest_version_of_secret(
                path=path, mount_point=mount_point
            )
            print("Latest version marked as deleted.")
        elif choice == "2":
            client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=path, mount_point=mount_point
            )
            print("All versions and metadata removed.")
        else:
            print("Unknown choice, nothing deleted.")
    except Exception as exc:  # noqa: BLE001
        print(f"Error deleting secret: {exc}")


def main() -> None:
    config = prompt_vault_config()
    client = authenticate(config)
    mount_point = config["mount_point"]

    while True:
        print(
            """
===== nvimvt =====
1) List secrets
2) Read secret
3) Create/Update secret (new version)
4) Delete secret
5) Exit
"""
        )
        choice = input("Select an action: ").strip()
        if choice == "1":
            list_secrets(client, mount_point)
        elif choice == "2":
            read_secret(client, mount_point)
        elif choice == "3":
            write_secret(client, mount_point)
        elif choice == "4":
            delete_secret(client, mount_point)
        elif choice == "5":
            print("Goodbye!")
            break
        else:
            print("Unknown choice, please use 1-5.")


if __name__ == "__main__":
    main()
