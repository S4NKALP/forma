import subprocess
from typing import NamedTuple

from fabric.utils import logger


class CommandResult(NamedTuple):
    returncode: int
    stdout: str | bytes
    stderr: str | bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(
    args: list[str],
    *,
    timeout: float | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    input: str | bytes | None = None,
    text: bool = True,
) -> CommandResult:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=text,
            timeout=timeout,
            cwd=cwd,
            env=env,
            input=input,
        )

        return CommandResult(
            result.returncode,
            result.stdout,
            result.stderr,
        )

    except subprocess.TimeoutExpired as exc:
        logger.warning(f"Command timed out: {args}")
        return CommandResult(
            124,
            exc.stdout or ("" if text else b""),
            exc.stderr or ("Command timed out" if text else b"Command timed out"),
        )

    except FileNotFoundError as exc:
        logger.warning(f"Command not found: {args[0]}")

        return CommandResult(
            127,
            "" if text else b"",
            str(exc),
        )

    except OSError as exc:
        logger.warning(f"Command failed: {exc}")

        return CommandResult(
            1,
            "" if text else b"",
            str(exc),
        )
