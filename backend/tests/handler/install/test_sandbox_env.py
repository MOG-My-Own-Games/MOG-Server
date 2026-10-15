from handler.install import sandbox
from handler.install.sandbox import SandboxSpec, build_bwrap_command, sandbox_environ

SECRETS = {
    "MOG_ADMIN_PASSWORD": "hunter2",
    "AUTH_SECRET_KEY": "k",
    "IGDB_CLIENT_SECRET": "s",
    "STEAMGRIDDB_API_KEY": "a",
}


def test_secrets_of_the_server_are_not_given_to_the_installer(monkeypatch):
    """The installer is untrusted code whose output a client downloads: it must not see the server's settings."""
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("WINEDEBUG", "-all")
    env = sandbox_environ()
    assert not set(SECRETS) & set(env)
    assert env["PATH"] == "/usr/bin"
    assert env["WINEDEBUG"] == "-all"


def test_command_clears_the_environment_before_setting_any(monkeypatch):
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    spec = SandboxSpec(installer_path="/lib/x.exe", work_dir="/w", proton_prefix="/p", display=":9")
    argv = build_bwrap_command(spec, ["wine", "/lib/x.exe"])
    assert "--clearenv" in argv
    assert argv.index("--clearenv") < argv.index("--setenv")
    assert not any(value in argv for value in SECRETS.values())
    assert not any(name in argv for name in SECRETS)


def test_explicit_environment_overrides_the_default(monkeypatch):
    assert sandbox_environ({"PATH": "/x", "MOG_ADMIN_PASSWORD": "p", "LC_ALL": "C"}) == {"PATH": "/x", "LC_ALL": "C"}
    assert sandbox is not None
