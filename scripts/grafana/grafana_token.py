#!/usr/bin/env python3
"""Keychain-backed Grafana admin tasks for the local brew Grafana (127.0.0.1:3000). Never prints secrets.

  init-admin   first run only: change the default admin password (admin/admin) to a random one stored in the
               macOS Keychain (service open-ai-chip-grafana, account admin); no-op if the Keychain entry works
  mcp-token    create the Viewer service account "hermes-chip-mcp" and a token, stored in the Keychain
               (service open-ai-chip-grafana-mcp); no-op if the stored token still authenticates
  check        exit 0 if the Keychain admin password and the MCP token both authenticate

Docs: docs/GRAFANA.md.
"""
import base64
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request

URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
SVC_ADMIN = "open-ai-chip-grafana"
SVC_MCP = "open-ai-chip-grafana-mcp"
SA_NAME = "hermes-chip-mcp"


def kc_get(service, account=None):
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    if account:
        cmd[2:2] = ["-a", account]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def kc_set(service, account, secret):
    # -w with the value puts it in argv for an instant; use stdin form: "security -i" reads commands from stdin
    line = "add-generic-password -a %s -s %s -w %s -U\n" % (account, service, secret)
    r = subprocess.run(["security", "-i"], input=line, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("keychain write failed for service %s" % service)


def call(method, path, auth, body=None):
    req = urllib.request.Request(URL + path, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def basic(pw):
    return "Basic " + base64.b64encode(("admin:" + pw).encode()).decode()


def init_admin():
    pw = kc_get(SVC_ADMIN, "admin")
    if pw and call("GET", "/api/user", basic(pw))[0] == 200:
        print("admin password: Keychain entry works (unchanged)")
        return
    new = secrets.token_urlsafe(24)
    code, _ = call("PUT", "/api/user/password", basic("admin"),
                   {"oldPassword": "admin", "newPassword": new, "confirmNew": new})
    if code != 200:
        sys.exit("cannot change the admin password: default admin/admin no longer works and the Keychain entry "
                 "does not authenticate; reset it with `grafana cli admin reset-admin-password` and store the "
                 "result with security add-generic-password -a admin -s %s -U -w" % SVC_ADMIN)
    kc_set(SVC_ADMIN, "admin", new)
    print("admin password: changed from the default, stored in the Keychain (service %s)" % SVC_ADMIN)


def mcp_token():
    tok = kc_get(SVC_MCP)
    if tok and call("GET", "/api/user", "Bearer " + tok)[0] == 200:
        print("mcp token: Keychain token works (unchanged)")
        return
    pw = kc_get(SVC_ADMIN, "admin")
    if not pw:
        sys.exit("no admin password in the Keychain; run init-admin first")
    auth = basic(pw)
    code, found = call("GET", "/api/serviceaccounts/search?query=" + SA_NAME, auth)
    sa = next((s for s in found.get("serviceAccounts", []) if s.get("login", "").endswith(SA_NAME)
               or s.get("name") == SA_NAME), None)
    if sa is None:
        code, sa = call("POST", "/api/serviceaccounts", auth, {"name": SA_NAME, "role": "Viewer"})
        if code != 201:
            sys.exit("cannot create the service account (HTTP %s)" % code)
    code, t = call("POST", "/api/serviceaccounts/%s/tokens" % sa["id"], auth,
                   {"name": "mcp-%s" % secrets.token_hex(3)})
    if code != 200 or "key" not in t:
        sys.exit("cannot create the token (HTTP %s)" % code)
    kc_set(SVC_MCP, SA_NAME, t["key"])
    print("mcp token: Viewer service account %s, token stored in the Keychain (service %s)" % (SA_NAME, SVC_MCP))


def check():
    pw = kc_get(SVC_ADMIN, "admin")
    tok = kc_get(SVC_MCP)
    ok_a = bool(pw) and call("GET", "/api/user", basic(pw))[0] == 200
    ok_t = bool(tok) and call("GET", "/api/user", "Bearer " + tok)[0] == 200
    print("admin keychain login: %s; mcp token: %s" % ("ok" if ok_a else "FAIL", "ok" if ok_t else "FAIL"))
    return 0 if ok_a and ok_t else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init-admin":
        init_admin()
    elif cmd == "mcp-token":
        mcp_token()
    elif cmd == "check":
        sys.exit(check())
    else:
        sys.exit(__doc__)
