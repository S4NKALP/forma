import configparser
import fcntl
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import tomlkit
from fabric.core.service import Property, Service, Signal


class AppImageService(Service):
    @Signal
    def changed(self) -> None: ...

    @Signal
    def installed(self, slug: str, name: str) -> None: ...

    @Signal
    def removed(self, slug: str) -> None: ...

    @Signal
    def renamed(self, slug: str, name: str) -> None: ...

    @Property(bool, default_value=False)
    def installing(self) -> bool:
        return self._installing

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        data_home = Path(
            os.environ.get(
                "XDG_DATA_HOME",
                Path.home() / ".local/share",
            )
        )

        self.apps_dir = data_home / "applications"
        self.forma_dir = data_home / "forma"
        self.desktop_dir = data_home / "applications"
        self.icon_dir = self.forma_dir / "appimages"

        self.registry = self.forma_dir / "appimages.toml"
        self.lock_file = self.forma_dir / "appimages.lock"

        self._installing = False

        self.apps_dir.mkdir(parents=True, exist_ok=True)
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        self.forma_dir.mkdir(parents=True, exist_ok=True)

        if not self.registry.exists():
            self._write_registry(tomlkit.document())

    @Property(list)
    def apps(self) -> list[dict]:
        data = self._read_registry()
        return [
            {
                "slug": slug,
                **dict(app),
            }
            for slug, app in data.get("apps", {}).items()
        ]

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with self.lock_file.open("w") as file:
            fcntl.flock(file, fcntl.LOCK_EX)

            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def _read_registry(self) -> tomlkit.TOMLDocument:
        try:
            return tomlkit.parse(self.registry.read_text(encoding="utf-8"))
        except OSError, tomlkit.exceptions.TOMLDecodeError:
            return tomlkit.document()

    def _write_registry(
        self,
        data: tomlkit.TOMLDocument,
    ) -> None:
        fd, temporary = tempfile.mkstemp(
            dir=self.forma_dir,
            prefix=".appimages-",
            suffix=".tmp",
        )

        try:
            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
            ) as file:
                file.write(tomlkit.dumps(data))

            os.replace(temporary, self.registry)

        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _parse_desktop(
        self,
        path: Path,
    ) -> dict[str, str]:
        parser = configparser.ConfigParser(
            interpolation=None,
            strict=False,
            delimiters=("=",),
            comment_prefixes=(),
            inline_comment_prefixes=(),
        )

        parser.optionxform = str

        try:
            with path.open(
                encoding="utf-8",
                errors="replace",
            ) as file:
                parser.read_file(file)
        except OSError, configparser.Error:
            return {}

        if not parser.has_section("Desktop Entry"):
            return {}

        return {key: value.strip() for key, value in parser["Desktop Entry"].items()}

    def _desktop_value(self, value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\t", "\\t")
            .replace("\r", "\\r")
        )

    def _desktop_exec(self, path: Path) -> str:
        value = str(path).replace("\\", "\\\\")

        if any(char.isspace() or char in "\"'`$;&|<>*?#()" for char in value):
            return f'"{value.replace(chr(34), chr(92) + chr(34))}"'

        return value

    def _write_desktop(
        self,
        path: Path,
        *,
        name: str,
        executable: Path,
        icon: str = "",
        categories: str = "",
        wmclass: str = "",
    ) -> None:
        lines = [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={self._desktop_value(name)}",
            f"Exec={self._desktop_exec(executable)} %U",
        ]

        if icon:
            lines.append(f"Icon={self._desktop_value(icon)}")

        if categories:
            lines.append(f"Categories={self._desktop_value(categories)}")

        if wmclass:
            lines.append(f"StartupWMClass={self._desktop_value(wmclass)}")

        lines.extend(
            [
                "Terminal=false",
                "X-Forma-AppImage=true",
            ]
        )

        path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    def _update_desktop_database(self) -> None:
        try:
            subprocess.run(
                [
                    "update-desktop-database",
                    str(self.desktop_dir),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except OSError, subprocess.SubprocessError:
            pass

    def _is_appimage(self, path: Path) -> bool:
        if not path.is_file() or path.suffix.lower() != ".appimage":
            return False

        try:
            with path.open("rb") as file:
                return file.read(4) == b"\x7fELF"
        except OSError:
            return False

    def _slugify(self, name: str) -> str:
        name = re.sub(
            r"\.(?i:appimage)$",
            "",
            name,
        )

        tokens = re.split(
            r"[._\-\s]+",
            name,
        )

        arch = {
            "x86_64",
            "amd64",
            "x86",
            "i386",
            "i686",
            "aarch64",
            "arm64",
            "armhf",
            "arm",
            "linux",
            "gnu",
            "glibc",
            "musl",
            "static",
            "portable",
        }

        tokens = [
            token
            for token in tokens
            if token
            and token.lower() not in arch
            and not re.fullmatch(
                r"[vV]?\d+(?:\.\d+)*",
                token,
            )
        ]

        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            "-".join(tokens).lower(),
        ).strip("-")

        return slug or "app"

    def _entry_fields(self, desktop_file: Path) -> tuple[str, str, str, str]:
        name = icon = categories = wmclass = ""
        try:
            for line in desktop_file.read_text().splitlines():
                if line.startswith("Name=") and not name:
                    name = line.split("=", 1)[1]
                elif line.startswith("Icon=") and not icon:
                    icon = line.split("=", 1)[1]
                elif line.startswith("Categories=") and not categories:
                    categories = line.split("=", 1)[1]
                elif line.startswith("StartupWMClass=") and not wmclass:
                    wmclass = line.split("=", 1)[1]
        except OSError:
            pass
        return name.strip(), icon.strip(), categories.strip(), wmclass.strip()

    def _walk(self, root: Path, maxdepth: int):
        for dirpath, dirnames, filenames in os.walk(root):
            rel = Path(dirpath).relative_to(root).parts
            depth = len(rel)
            if depth > maxdepth:
                dirnames[:] = []
                continue
            for f in filenames:
                yield Path(dirpath) / f

    def _install_icon(self, root: Path, iconname: str, slug: str) -> str:
        for ext in ("png", "svg"):
            try:
                (self.icon_dir / f"{slug}.{ext}").unlink()
            except FileNotFoundError:
                pass

        if "/" in iconname:
            iconname = iconname.rsplit("/", 1)[-1]
        base, dot, _ext = iconname.rpartition(".")
        if dot and _ext.lower() in ("png", "svg", "xpm"):
            iconname = base

        found = None
        if iconname:
            svg = root.rglob(f"**/scalable/{iconname}.svg")
            found = next(svg, None)
            if found is None:
                for size in (
                    "1024x1024",
                    "512x512",
                    "256x256",
                    "128x128",
                    "96x96",
                    "64x64",
                    "48x48",
                ):
                    cand = root.rglob(f"**/{size}/{iconname}.png")
                    found = next(cand, None)
                    if found is not None:
                        break
            if found is None:
                found = next(root.rglob(f"**/{iconname}.svg"), None)
                if found is None:
                    found = next(root.rglob(f"**/{iconname}.png"), None)

        if found is None and (root / ".DirIcon").exists():
            try:
                res = (root / ".DirIcon").resolve()
            except OSError:
                res = root / ".DirIcon"
            found = res if res.is_file() else root / ".DirIcon"

        if found is None:
            found = next(
                (
                    p
                    for p in self._walk(root, 1)
                    if p.suffix.lower() in (".png", ".svg")
                ),
                None,
            )

        if found is None or not found.is_file():
            return ""

        ext = "svg" if found.suffix.lower() == ".svg" else "png"
        dest = self.icon_dir / f"{slug}.{ext}"
        shutil.copyfile(found, dest)
        return str(dest)

    def install(self, source: str | Path) -> str:
        source = Path(source).expanduser()

        if not self._is_appimage(source):
            raise ValueError(f"not an appimage: {source}")

        self._installing = True
        self.notify("installing")

        try:
            source = source.resolve()
            destination = self.apps_dir / source.name
            slug = self._slugify(source.name)

            if source != destination:
                shutil.copy2(source, destination)

            destination.chmod(0o755)

            name = iconname = categories = wmclass = ""
            icon_path = ""

            with tempfile.TemporaryDirectory() as tmp:
                tmpd = Path(tmp)
                try:
                    subprocess.run(
                        [str(destination), "--appimage-extract"],
                        cwd=tmpd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=60,
                    )
                except OSError, subprocess.SubprocessError:
                    pass

                sr = tmpd / "squashfs-root"
                if sr.is_dir():
                    df = next(
                        (p for p in self._walk(sr, 2) if p.suffix == ".desktop"), None
                    )
                    if df is not None:
                        name, iconname, categories, wmclass = self._entry_fields(df)
                    icon_path = self._install_icon(sr, iconname, slug)

            if not name:
                name = self._pretty_name(source.name)
            appid = wmclass or name or slug

            with self._lock():
                data = self._read_registry()
                apps = data.setdefault(
                    "apps",
                    tomlkit.table(),
                )

                previous = apps.get(slug, {})
                previous_path = str(
                    previous.get(
                        "appimagePath",
                        "",
                    )
                )

                action = "new"

                if previous_path:
                    if Path(previous_path) == destination:
                        action = "reinstalled"

                    elif not previous.get("appId") or previous.get("appId") == appid:
                        action = "updated"

                        try:
                            Path(previous_path).unlink()
                        except OSError:
                            pass

                    else:
                        number = 2

                        while apps.get(
                            f"{slug}-{number}",
                            {},
                        ).get("appimagePath"):
                            number += 1

                        slug = f"{slug}-{number}"
                        action = "new"

                desktop = self.desktop_dir / f"forma-{slug}.desktop"

                self._write_desktop(
                    desktop,
                    name=name,
                    executable=destination,
                    icon=icon_path,
                    categories=categories,
                    wmclass=wmclass,
                )

                apps[slug] = {
                    "name": name,
                    "appimagePath": str(destination),
                    "iconPath": icon_path,
                    "desktopPath": str(desktop),
                    "appId": appid,
                }

                data["version"] = 1
                self._write_registry(data)

            self._update_desktop_database()

            self.changed()
            self.notify("apps")
            self.installed(slug, name)

            return f"{slug}\t{name}\t{action}"

        finally:
            self._installing = False
            self.notify("installing")

    def remove(self, slug: str) -> None:
        with self._lock():
            data = self._read_registry()
            apps = data.get("apps", {})
            entry = apps.get(slug)

            if not entry:
                return

            for key in (
                "appimagePath",
                "iconPath",
                "desktopPath",
            ):
                path = Path(str(entry.get(key, "")))

                try:
                    path.unlink()
                except OSError:
                    pass

            apps.pop(slug, None)
            self._write_registry(data)

        self._update_desktop_database()

        self.changed()
        self.notify("apps")
        self.removed(slug)

    def rename(
        self,
        slug: str,
        name: str,
    ) -> None:
        if not name:
            raise ValueError("name cannot be empty")

        with self._lock():
            data = self._read_registry()
            apps = data.get("apps", {})
            entry = apps.get(slug)

            if not entry:
                raise KeyError(slug)

            desktop = Path(str(entry["desktopPath"]))

            fields = self._parse_desktop(desktop)

            self._write_desktop(
                desktop,
                name=name,
                executable=Path(str(entry["appimagePath"])),
                icon=str(entry.get("iconPath", "")),
                categories=fields.get(
                    "Categories",
                    "",
                ),
                wmclass=fields.get(
                    "StartupWMClass",
                    "",
                ),
            )

            entry["name"] = name
            apps[slug] = entry

            self._write_registry(data)

        self._update_desktop_database()

        self.changed()
        self.notify("apps")
        self.renamed(slug, name)

    def _pretty_name(self, filename: str) -> str:
        filename = re.sub(
            r"\.(?i:appimage)$",
            "",
            filename,
        )

        return re.sub(
            r"[\._-]+",
            " ",
            filename,
        ).strip()


appimage_service = AppImageService()
