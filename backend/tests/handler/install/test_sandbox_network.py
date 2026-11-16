from handler.install.sandbox import NETWORK_ETC_FILES, SandboxSpec, build_bwrap_command


def argv(**over):
    spec = SandboxSpec(installer_path="/lib/x.exe", work_dir="/w", proton_prefix="/p", display=":9", **over)
    return build_bwrap_command(spec, ["wine", "/lib/x.exe"])


def test_the_sandbox_has_no_network_unless_asked():
    args = argv()
    assert "--unshare-net" in args
    assert not any(etc in args for etc in NETWORK_ETC_FILES)


def test_an_installer_that_may_use_the_network_shares_it_and_can_resolve_names():
    """An installer that downloads extra files stopped on 'Cannot connect' inside the network-less sandbox."""
    args = argv(network=True)
    assert "--unshare-net" not in args
    for etc in NETWORK_ETC_FILES:
        assert args[args.index(etc) - 1] == "--ro-bind-try"
    assert "--unshare-user" in args and "--clearenv" in args  # the rest of the confinement stays
