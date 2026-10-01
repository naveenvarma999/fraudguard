import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["success", "failed-check"])
def test_checked_deploy_rolls_back_on_failed_gate(tmp_path, scenario):
    bash = (
        Path("C:/Program Files/Git/bin/bash.exe")
        if os.name == "nt"
        else Path(shutil.which("bash") or "/missing")
    )
    if not bash.exists():
        pytest.skip("Bash unavailable")
    root = tmp_path / "project"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/Deploy-Checked.sh"
    shutil.copyfile(source, scripts / source.name)
    (root / "compose.yaml").write_text("services: {}")
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "python3").write_text("#!/usr/bin/env bash\nexit 0\n", newline="\n")
    (fake / "docker").write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$MOCK_LOG"
if [[ "$1" == "inspect" ]]; then printf 'sha256:%064d\\n' 0; exit 0; fi
if [[ "$*" == *"ps -q api" ]]; then echo existing-api; exit 0; fi
if [[ "$*" == *"ps -q monitor" ]]; then echo existing-monitor; exit 0; fi
if [[ "$*" == *"exec -T api python -m fraudguard.deployment"* && "$SCENARIO" == "failed-check" ]]; then exit 1; fi
exit 0
""",
        newline="\n",
    )
    for p in fake.iterdir():
        p.chmod(0o755)
    log = tmp_path / "docker.log"
    env = {**os.environ, "SCENARIO": scenario}
    command = 'export PATH="$1:$PATH"; export MOCK_LOG="$2"; bash "$3"'
    if os.name == "nt":
        command = 'export PATH="$(cygpath -u "$1"):$PATH"; export MOCK_LOG="$(cygpath -u "$2")"; bash "$3"'
    result = subprocess.run(
        [str(bash), "-c", command, "test", str(fake), str(log), str(scripts / source.name)],
        env=env,
        capture_output=True,
        text=True,
    )
    commands = log.read_text()
    if scenario == "success":
        assert result.returncode == 0, result.stderr
        assert not (root / "compose.rollback.yaml").exists()
    else:
        assert result.returncode == 1, result.stderr
        assert "sha256:" in (root / "compose.rollback.yaml").read_text()
        assert "-f compose.rollback.yaml up -d --no-build --pull never api" in commands
        assert "-f compose.rollback.yaml up -d --no-build --pull never monitor" in commands
    assert "--require-backup" in commands
