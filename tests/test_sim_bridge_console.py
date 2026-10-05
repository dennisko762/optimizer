from sim_bridge.main import server_banner_lines


def test_server_banner_is_windows_console_safe() -> None:
    lines = server_banner_lines()

    assert "EFB Server" in "\n".join(lines)
    for line in lines:
        line.encode("cp1252")
