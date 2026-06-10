import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import webview


if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "pvshot_config.json"
PREPARE_SCRIPT = SCRIPT_DIR / "prepare_pvshot_environment.ps1"
SCREENSHOT_SCRIPT = SCRIPT_DIR / "screenshot_openfoam_slices.py"


DEFAULT_SETTINGS = {
    "time_mode": "selected",
    "selected_times": [0.0, 0.0015, 0.003, 0.0045, 0.006, 0.00699],
    "time_stride": 10,
    "time_range": [0.0, 0.00699],
    "color_range_mode": "custom",
    "use_color_range": True,
    "color_range": [0.0, 1.0],
}

RUN_OPTION_DEFAULTS = {
    "pvsm_state_file": "",
    "batch_enabled": False,
    "batch_case_root": "",
    "output_root": "",
}

OBSOLETE_CONFIG_KEYS = {
    "style_source",
    "field_policy",
    "state_scalar_filter_policy",
}


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def save_config(config):
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def is_ascii_path(path_text):
    try:
        path_text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def is_openfoam_case(path):
    if not path:
        return False
    root = Path(path)
    return (root / "constant" / "polyMesh").exists() and (root / "system" / "controlDict").exists()


def safe_case_name(name):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", name)
    return cleaned or "case"


def writable_directory(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".pvshot_write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return True
    except OSError:
        return False


def work_root():
    for root in [Path("D:/pvshot_work"), Path("C:/pvshot_work")]:
        if root.anchor and Path(root.anchor).exists() and writable_directory(root):
            return root.resolve()
    raise RuntimeError("No writable work root found. Tried D:/pvshot_work and C:/pvshot_work")


def add_pvpython_candidate(candidates, value):
    if not value:
        return
    text = str(value).strip().strip('"')
    path = Path(text)
    if path.is_dir():
        maybe = path / "bin" / "pvpython.exe"
        if maybe.exists():
            candidates.append(maybe.resolve())
        return
    if path.is_file():
        if path.name.lower() == "pvpython.exe":
            candidates.append(path.resolve())
        elif path.name.lower() == "paraview.exe":
            maybe = path.parent / "pvpython.exe"
            if maybe.exists():
                candidates.append(maybe.resolve())


def find_pvpython():
    candidates = []
    found = shutil.which("pvpython.exe")
    add_pvpython_candidate(candidates, found)

    try:
        import winreg

        registry_roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, root_name in registry_roots:
            try:
                root = winreg.OpenKey(hive, root_name)
            except OSError:
                continue
            with root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        subkey_name = winreg.EnumKey(root, index)
                        subkey = winreg.OpenKey(root, subkey_name)
                    except OSError:
                        continue
                    with subkey:
                        try:
                            display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                        except OSError:
                            display_name = ""
                        if "ParaView" not in str(display_name):
                            continue
                        for value_name in ["InstallLocation", "DisplayIcon"]:
                            try:
                                add_pvpython_candidate(candidates, winreg.QueryValueEx(subkey, value_name)[0])
                            except OSError:
                                pass
    except ImportError:
        pass

    for root in [Path("C:/Program Files"), Path("C:/Program Files (x86)"), Path("D:/"), Path("E:/")]:
        if not root.exists():
            continue
        for child in root.glob("ParaView*"):
            add_pvpython_candidate(candidates, child)
            add_pvpython_candidate(candidates, child / "bin" / "pvpython.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("Cannot find pvpython.exe")


def paraview_version_from_path(pvpython):
    match = re.search(r"ParaView\s*([0-9]+(?:\.[0-9]+){1,3})", pvpython)
    return match.group(1) if match else "unknown"


def find_case_under_script_dir():
    if is_openfoam_case(SCRIPT_DIR):
        return str(SCRIPT_DIR)

    candidates = []
    for depth_root in [SCRIPT_DIR, SCRIPT_DIR / "示例"]:
        if not depth_root.exists():
            continue
        for child in depth_root.rglob("*"):
            if child.is_dir() and is_openfoam_case(child):
                candidates.append(str(child))
    return sorted(candidates)[0] if candidates else ""


def prepare_environment_python(case_dir="", output_dir=""):
    original_case = Path(case_dir or find_case_under_script_dir()).resolve()
    if not is_openfoam_case(original_case):
        raise RuntimeError(f"Not an OpenFOAM case: {original_case}")

    root = work_root()
    path_was_non_ascii = not is_ascii_path(str(original_case))
    safe_name = safe_case_name(original_case.name)

    if path_was_non_ascii:
        prepared_case = root / "cases" / safe_name
        prepared_case.mkdir(parents=True, exist_ok=True)
        shutil.copytree(original_case, prepared_case, dirs_exist_ok=True)
        case_was_copied = True
    else:
        prepared_case = original_case
        case_was_copied = False

    foam_files = sorted(prepared_case.glob("*.foam"))
    if foam_files:
        case_file = foam_files[0]
        foam_created = False
    else:
        case_file = prepared_case / f"{safe_name}.foam"
        case_file.write_text("", encoding="ascii")
        foam_created = True

    if output_dir:
        final_output = Path(output_dir).resolve()
    else:
        final_output = root / "output" / safe_name
    final_output.mkdir(parents=True, exist_ok=True)

    existing = load_config()
    config = {
        "pvpython_path": find_pvpython(),
        "paraview_version": "",
        "original_case_dir": str(original_case),
        "case_dir": str(prepared_case.resolve()),
        "case_file": str(case_file.resolve()),
        "output_dir": str(final_output.resolve()),
        "path_was_non_ascii": path_was_non_ascii,
        "case_was_copied": case_was_copied,
        "foam_file_was_created": foam_created,
    }
    config["paraview_version"] = paraview_version_from_path(config["pvpython_path"])
    if "screenshot_settings" in existing:
        config["screenshot_settings"] = existing["screenshot_settings"]
    for key in RUN_OPTION_DEFAULTS:
        if key in existing:
            config[key] = existing[key]
    if "batch_cases" in existing:
        config["batch_cases"] = existing["batch_cases"]
    save_config(config)
    return config


def prepared_case_record(case_dir, output_dir):
    original_case = Path(case_dir).resolve()
    if not is_openfoam_case(original_case):
        raise RuntimeError(f"Not an OpenFOAM case: {original_case}")

    root = work_root()
    safe_name = safe_case_name(original_case.name)
    path_was_non_ascii = not is_ascii_path(str(original_case))

    if path_was_non_ascii:
        prepared_case = root / "cases" / safe_name
        prepared_case.mkdir(parents=True, exist_ok=True)
        shutil.copytree(original_case, prepared_case, dirs_exist_ok=True)
        case_was_copied = True
    else:
        prepared_case = original_case
        case_was_copied = False

    foam_files = sorted(prepared_case.glob("*.foam"))
    if foam_files:
        case_file = foam_files[0]
        foam_created = False
    else:
        case_file = prepared_case / f"{safe_name}.foam"
        case_file.write_text("", encoding="ascii")
        foam_created = True

    final_output = Path(output_dir).resolve()
    final_output.mkdir(parents=True, exist_ok=True)
    return {
        "case_name": safe_name,
        "original_case_dir": str(original_case),
        "case_dir": str(prepared_case.resolve()),
        "case_file": str(case_file.resolve()),
        "output_dir": str(final_output),
        "path_was_non_ascii": path_was_non_ascii,
        "case_was_copied": case_was_copied,
        "foam_file_was_created": foam_created,
    }


def discover_openfoam_cases(root_dir):
    root = Path(root_dir).resolve()
    if not root.exists():
        raise RuntimeError(f"Batch root does not exist: {root}")
    cases = []
    if is_openfoam_case(root):
        cases.append(root)
    for system_dir in root.rglob("system"):
        case_dir = system_dir.parent
        if is_openfoam_case(case_dir):
            cases.append(case_dir)
    unique = []
    seen = set()
    for case in sorted(cases, key=lambda item: str(item).lower()):
        resolved = str(case.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(case)
    return unique


def prepare_batch_environment_python(batch_root, output_root):
    cases = discover_openfoam_cases(batch_root)
    if not cases:
        raise RuntimeError(f"No OpenFOAM cases found under: {batch_root}")

    root = work_root()
    final_output_root = Path(output_root).resolve() if output_root else root / "output_batch"
    final_output_root.mkdir(parents=True, exist_ok=True)

    records = []
    used_names = {}
    for case in cases:
        base_name = safe_case_name(case.name)
        index = used_names.get(base_name, 0)
        used_names[base_name] = index + 1
        case_name = base_name if index == 0 else f"{base_name}_{index + 1}"
        record = prepared_case_record(case, final_output_root / case_name)
        record["case_name"] = case_name
        records.append(record)
    return records, final_output_root


def parse_float_folder(name):
    try:
        return float(name)
    except ValueError:
        return None


def openfoam_field_class(path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r"\bclass\s+([A-Za-z0-9_]+)\s*;", text)
    return match.group(1) if match else ""


def is_vector_field_class(class_name):
    return class_name in {
        "volVectorField",
        "surfaceVectorField",
        "pointVectorField",
        "VectorField",
    }


def read_mesh_bounds(case_root):
    points_file = Path(case_root) / "constant" / "polyMesh" / "points"
    if not points_file.exists():
        return None
    try:
        data = points_file.read_bytes()
    except OSError:
        return None

    header = data[: min(len(data), 4096)].decode("utf-8", errors="ignore")
    is_binary = re.search(r"\bformat\s+binary\s*;", header) is not None
    scalar_size = 4 if "scalar=32" in header else 8

    if is_binary:
        match = re.search(rb"\n\s*(\d+)\s*\n\s*\(", data)
        if not match:
            return None
        point_count = int(match.group(1))
        offset = match.end()
        step = scalar_size * 3
        payload = data[offset : offset + point_count * step]
        if len(payload) < point_count * step:
            return None
        fmt = "<fff" if scalar_size == 4 else "<ddd"
        xmin = ymin = zmin = float("inf")
        xmax = ymax = zmax = float("-inf")
        for x, y, z in struct.iter_unpack(fmt, payload):
            xmin = min(xmin, x)
            xmax = max(xmax, x)
            ymin = min(ymin, y)
            ymax = max(ymax, y)
            zmin = min(zmin, z)
            zmax = max(zmax, z)
        return {
            "min": [xmin, ymin, zmin],
            "max": [xmax, ymax, zmax],
            "size": [xmax - xmin, ymax - ymin, zmax - zmin],
            "point_count": point_count,
        }

    text = data.decode("utf-8", errors="ignore")
    triples = re.findall(
        r"\(\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
        r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
        r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*\)",
        text,
    )
    if not triples:
        return None

    xs = [float(item[0]) for item in triples]
    ys = [float(item[1]) for item in triples]
    zs = [float(item[2]) for item in triples]
    return {
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)],
        "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
        "point_count": len(triples),
    }


def scan_case(case_dir):
    if not case_dir or not Path(case_dir).exists():
        return {
            "case_dir": case_dir or "",
            "is_openfoam_case": False,
            "path_is_ascii": True,
            "foam_files": [],
            "times": [],
            "fields": [],
            "field_types": {},
            "vector_fields": [],
            "mesh_bounds": None,
        }

    root = Path(case_dir)
    numeric_times = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        value = parse_float_folder(child.name)
        if value is not None:
            numeric_times.append((value, child))

    numeric_times.sort(key=lambda item: item[0])
    fields = []
    field_types = {}
    for _, time_dir in numeric_times:
        field_files = sorted(
            item for item in time_dir.iterdir() if item.is_file() and item.name != "uniform"
        )
        fields = [item.name for item in field_files]
        field_types = {item.name: openfoam_field_class(item) for item in field_files}
        if fields:
            break
    vector_fields = [name for name in fields if is_vector_field_class(field_types.get(name, ""))]

    return {
        "case_dir": str(root),
        "is_openfoam_case": is_openfoam_case(root),
        "path_is_ascii": is_ascii_path(str(root)),
        "foam_files": sorted(item.name for item in root.glob("*.foam")),
        "times": [value for value, _ in numeric_times],
        "fields": fields,
        "field_types": field_types,
        "vector_fields": vector_fields,
        "mesh_bounds": read_mesh_bounds(root),
    }


def merge_settings(raw_settings):
    merged = dict(DEFAULT_SETTINGS)
    for key, value in (raw_settings or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def parse_number_list(value):
    if isinstance(value, list):
        return [float(item) for item in value]
    parts = [part.strip() for part in re.split(r"[,;\s]+", str(value or "")) if part.strip()]
    return [float(part) for part in parts]


def parse_string_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[,;]+", str(value or "")) if part.strip()]


def normalize_color_range_mode(value):
    aliases = {
        "fixed": "custom",
        "manual": "custom",
        "auto_each": "data",
        "single-frame": "data",
        "single_frame": "data",
        "auto_all": "all_times",
        "unified": "all_times",
        "state": "state_file",
        "state_range": "state_file",
    }
    mode = str(value or "custom").strip().lower()
    return aliases.get(mode, mode)


def normalize_run_options(settings):
    data = dict(RUN_OPTION_DEFAULTS)
    for key in RUN_OPTION_DEFAULTS:
        if settings and key in settings and settings[key] is not None:
            data[key] = settings[key]

    normalized = {
        "pvsm_state_file": str(data["pvsm_state_file"] or "").strip(),
        "batch_enabled": bool(data["batch_enabled"]),
        "batch_case_root": str(data["batch_case_root"] or "").strip(),
        "output_root": str(data["output_root"] or "").strip(),
    }
    if not normalized["pvsm_state_file"]:
        raise ValueError("pvsm_state_file is required")
    if normalized["batch_enabled"] and not normalized["batch_case_root"]:
        raise ValueError("batch_case_root is required when batch mode is enabled")
    return normalized


def normalize_settings(settings):
    data = merge_settings(settings)
    normalized = {
        "time_mode": str(data["time_mode"]),
        "selected_times": parse_number_list(data["selected_times"]),
        "time_stride": int(data["time_stride"]),
        "time_range": parse_number_list(data["time_range"]),
        "color_range_mode": normalize_color_range_mode(data.get("color_range_mode")),
        "use_color_range": normalize_color_range_mode(data.get("color_range_mode")) == "custom",
        "color_range": parse_number_list(data["color_range"]),
    }

    required_lengths = {
        "time_range": 2,
        "color_range": 2,
    }
    for key, expected in required_lengths.items():
        if len(normalized[key]) != expected:
            raise ValueError(f"{key} must contain {expected} numbers")

    if normalized["color_range_mode"] not in ("state_file", "data", "custom", "all_times", "visible"):
        raise ValueError("color_range_mode must be state_file, data, custom, all_times, or visible")
    return normalized


def run_command(command, cwd=SCRIPT_DIR):
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        shell=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0,
    }


class PvshotApi:
    def __init__(self):
        self._lock = threading.Lock()

    def get_state(self):
        config = load_config()
        candidates = [
            config.get("original_case_dir"),
            config.get("case_dir"),
            find_case_under_script_dir(),
        ]
        case_dir = next(
            (candidate for candidate in candidates if candidate and Path(candidate).exists()),
            next((candidate for candidate in candidates if candidate), ""),
        )
        detected = scan_case(case_dir)
        settings = merge_settings(config.get("screenshot_settings"))
        run_options = dict(RUN_OPTION_DEFAULTS)
        for key in RUN_OPTION_DEFAULTS:
            if key in config:
                run_options[key] = config[key]
        if detected["times"]:
            settings["time_range"] = [detected["times"][0], detected["times"][-1]]
        return {
            "config": config,
            "detected": detected,
            "settings": settings,
            "run_options": run_options,
            "batch_cases": config.get("batch_cases", []),
            "script_dir": str(SCRIPT_DIR),
            "config_file": str(CONFIG_FILE),
        }

    def choose_case_dir(self):
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else ""

    def choose_output_dir(self):
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else ""

    def choose_pvsm_file(self):
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("ParaView state (*.pvsm)", "All files (*.*)"),
        )
        return result[0] if result else ""

    def choose_batch_root(self):
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else ""

    def prepare_environment(self, case_dir="", output_dir=""):
        try:
            config = prepare_environment_python(case_dir, output_dir)
        except Exception as exc:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(exc),
                "state": self.get_state(),
            }

        return {
            "ok": True,
            "stdout": (
                "Prepared pvshot environment:\n"
                f"  Config: {CONFIG_FILE}\n"
                f"  pvpython: {config['pvpython_path']}\n"
                f"  case: {config['case_dir']}\n"
                f"  output: {config['output_dir']}\n"
            ),
            "stderr": "",
            "state": self.get_state(),
        }

    def save_settings(self, settings):
        config = load_config()
        run_options = normalize_run_options(settings)
        for key in OBSOLETE_CONFIG_KEYS:
            config.pop(key, None)
        for key, value in run_options.items():
            config[key] = value
        config["screenshot_settings"] = normalize_settings(settings)
        save_config(config)
        return {"ok": True, "config": config}

    def prepare_batch_cases(self, batch_root="", output_root=""):
        try:
            records, final_output_root = prepare_batch_environment_python(batch_root, output_root)
            config = load_config()
            config["batch_case_root"] = str(Path(batch_root).resolve())
            config["output_root"] = str(final_output_root.resolve())
            config["batch_cases"] = records
            if records:
                first = records[0]
                config["original_case_dir"] = first["original_case_dir"]
                config["case_dir"] = first["case_dir"]
                config["case_file"] = first["case_file"]
                config["output_dir"] = first["output_dir"]
            if not config.get("pvpython_path"):
                config["pvpython_path"] = find_pvpython()
                config["paraview_version"] = paraview_version_from_path(config["pvpython_path"])
            save_config(config)
        except Exception as exc:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(exc),
                "state": self.get_state(),
            }

        return {
            "ok": True,
            "stdout": (
                f"Prepared {len(records)} batch case(s).\n"
                f"  Batch root: {batch_root}\n"
                f"  Output root: {final_output_root}\n"
            ),
            "stderr": "",
            "state": self.get_state(),
        }

    def run_screenshots(self, settings, case_dir="", output_dir=""):
        with self._lock:
            save_result = self.save_settings(settings)
            config = save_result["config"]
            batch_enabled = bool(config.get("batch_enabled"))

            if batch_enabled:
                prepared = self.prepare_batch_cases(
                    config.get("batch_case_root", ""),
                    config.get("output_root") or output_dir,
                )
                if not prepared["ok"]:
                    return prepared
                config = load_config()
            elif case_dir and case_dir != config.get("original_case_dir"):
                prepared = self.prepare_environment(case_dir, output_dir)
                if not prepared["ok"]:
                    return prepared
            elif output_dir:
                config = load_config()
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                config["output_dir"] = str(Path(output_dir).resolve())
                save_config(config)

            if not config.get("pvpython_path"):
                prepared = self.prepare_environment(case_dir or config.get("original_case_dir", ""), output_dir)
                if not prepared["ok"]:
                    return prepared
                config = load_config()

            pvpython = config["pvpython_path"]
            if not Path(pvpython).exists():
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"pvpython_path does not exist: {pvpython}",
                }

            result = run_command([pvpython, SCREENSHOT_SCRIPT.name])
            result["state"] = self.get_state()
            return result

    def open_output_dir(self):
        config = load_config()
        output_dir = config.get("output_dir")
        if output_dir and Path(output_dir).exists():
            os.startfile(output_dir)
            return {"ok": True}
        return {"ok": False, "error": "Output folder does not exist yet"}


HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>PvShot</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f6f8;
      --panel: #ffffff;
      --line: #d8dde6;
      --text: #172033;
      --muted: #657083;
      --accent: #2066d8;
      --accent-dark: #174fa8;
      --danger: #b3261e;
      --ok: #146c43;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }
    header {
      height: 52px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
    }
    main {
      display: grid;
      grid-template-columns: 390px minmax(520px, 1fr);
      gap: 14px;
      padding: 14px;
      min-height: calc(100vh - 52px);
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 14px;
      font-weight: 650;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin: 10px 0 5px;
    }
    label[title], input[title], select[title], textarea[title] {
      cursor: help;
    }
    label input[type="checkbox"] { width: auto; }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }
    #pvsmStateFile {
      direction: rtl;
      text-overflow: ellipsis;
    }
    #pvsmStateFile:focus {
      direction: ltr;
    }
    textarea {
      min-height: 68px;
      resize: vertical;
      font-family: Consolas, "Cascadia Mono", monospace;
    }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 8px 11px;
      font: inherit;
      cursor: pointer;
      white-space: nowrap;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      font-weight: 650;
    }
    button.primary:hover { background: var(--accent-dark); }
    button:disabled { opacity: 0.55; cursor: wait; }
    .row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: end;
    }
    .grid2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .grid3 {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
    }
    .checkrow {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .checkrow label {
      display: flex;
      align-items: center;
      gap: 7px;
      margin: 0;
      color: var(--text);
      font-size: 13px;
    }
    .checkrow input { width: auto; }
    .vector-options[hidden] { display: none; }
    .conditional[hidden] { display: none; }
    .collapsible-section { overflow: hidden; }
    .collapsible-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      user-select: none;
      margin: 0 0 12px;
    }
    .collapsible-header h2 {
      margin: 0;
      font-size: 14px;
      font-weight: 650;
    }
    .collapsible-icon {
      display: inline-block;
      transition: transform 0.2s ease;
      font-size: 12px;
      color: var(--muted);
    }
    .collapsible-section.collapsed .collapsible-icon {
      transform: rotate(-90deg);
    }
    .collapsible-content {
      overflow: hidden;
      transition: max-height 0.25s ease, opacity 0.2s ease;
      max-height: 600px;
      opacity: 1;
    }
    .collapsible-section.collapsed .collapsible-content {
      max-height: 0;
      opacity: 0;
    }
    .label-help {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .help-tip {
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border: 1px solid var(--line);
      border-radius: 50%;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
      font-weight: 700;
      cursor: help;
    }
    .help-tip:focus {
      outline: 2px solid rgba(37, 99, 235, 0.25);
      outline-offset: 2px;
    }
    .help-tip::after {
      content: attr(data-tip);
      position: absolute;
      z-index: 10;
      left: 50%;
      bottom: calc(100% + 10px);
      transform: translateX(-50%);
      width: min(420px, 80vw);
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #111827;
      color: #f8fafc;
      box-shadow: 0 12px 30px rgba(15, 23, 42, 0.25);
      font-size: 12px;
      font-weight: 400;
      line-height: 1.45;
      white-space: pre-line;
      text-align: left;
      pointer-events: none;
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.12s ease, visibility 0.12s ease;
    }
    .help-tip:hover::after,
    .help-tip:focus::after {
      opacity: 1;
      visibility: visible;
    }
    .facts {
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .fact strong {
      color: var(--text);
      font-weight: 600;
    }
    .status {
      color: var(--muted);
      font-size: 12px;
    }
    .status.ok { color: var(--ok); }
    .status.bad { color: var(--danger); }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    pre {
      margin: 0;
      padding: 11px;
      min-height: 180px;
      max-height: 300px;
      overflow: auto;
      background: #111827;
      color: #dbeafe;
      border-radius: 8px;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <header>
    <h1>PvShot</h1>
    <div id="topStatus" class="status">Ready</div>
  </header>
  <main>
    <div>
      <section>
        <h2>Paths</h2>
        <label>OpenFOAM case folder</label>
        <div class="row">
          <input id="caseDir" spellcheck="false">
          <button onclick="browseCase()">Browse</button>
        </div>
        <label>Output folder</label>
        <div class="row">
          <input id="outputDir" spellcheck="false">
          <button onclick="browseOutput()">Browse</button>
        </div>
        <div class="actions">
          <button class="primary" onclick="prepare()">Detect / Prepare</button>
          <button onclick="openOutput()">Open Output</button>
        </div>
      </section>
      <section>
        <h2>State Template</h2>
        <input id="styleSource" type="hidden" value="pvsm">
        <input id="fieldPolicy" type="hidden" value="state">
        <input id="stateScalarFilterPolicy" type="hidden" value="warn">
        <div id="pvsmOptions">
          <label title="ParaView 保存的 .pvsm state file。建议从代表性 case 调好样式后保存。">ParaView state file</label>
          <div class="row">
            <input id="pvsmStateFile" spellcheck="false" title="ParaView 保存的 .pvsm state file。建议从代表性 case 调好样式后保存。">
            <button onclick="browsePvsm()">Browse</button>
          </div>
        </div>
      </section>
      <section id="batchSection" class="collapsible-section collapsed">
        <div class="collapsible-header" onclick="toggleBatchSection()">
          <h2>Batch Cases</h2>
          <span class="collapsible-icon">&#9662;</span>
        </div>
        <div class="collapsible-content">
          <label title="启用后从父文件夹递归查找多个 OpenFOAM case，并按同一个 .pvsm 批量截图。"><input id="batchEnabled" type="checkbox" title="启用后从父文件夹递归查找多个 OpenFOAM case，并按同一个 .pvsm 批量截图。"> Enable batch</label>
          <label title="包含多个 OpenFOAM case 的父文件夹。程序会递归寻找 constant/polyMesh 与 system/controlDict。">Batch case root</label>
          <div class="row">
            <input id="batchCaseRoot" spellcheck="false" title="包含多个 OpenFOAM case 的父文件夹。程序会递归寻找 constant/polyMesh 与 system/controlDict。">
            <button onclick="browseBatchRoot()">Browse</button>
          </div>
          <label title="多 case 输出根目录。实际输出为 output_root/case_name/field_name/*.png。">Output root</label>
          <div class="row">
            <input id="outputRoot" spellcheck="false" title="多 case 输出根目录。实际输出为 output_root/case_name/field_name/*.png。">
            <button onclick="browseOutputRoot()">Browse</button>
          </div>
          <div class="actions">
            <button onclick="prepareBatch()">Scan / Prepare Batch</button>
          </div>
          <div class="facts">
            <div class="fact">Batch cases: <strong id="batchSummary">-</strong></div>
          </div>
        </div>
      </section>
      <section>
        <h2>Detected</h2>
        <div class="facts">
          <div class="fact">ParaView: <strong id="pvpython">-</strong></div>
          <div class="fact">Version: <strong id="pvversion">-</strong></div>
          <div class="fact">Prepared case: <strong id="preparedCase">-</strong></div>
          <div class="fact">.foam: <strong id="foamFile">-</strong></div>
          <div class="fact">Times: <strong id="timesSummary">-</strong></div>
          <div class="fact">Fields: <strong id="fieldsSummary">-</strong></div>
          <div class="fact">Mesh bounds: <strong id="boundsSummary">-</strong></div>
          <div class="fact">ASCII path: <strong id="asciiSummary">-</strong></div>
        </div>
      </section>
      <section>
        <h2>Run</h2>
        <div class="actions">
          <button class="primary" onclick="runShots()">Run Screenshots</button>
          <button onclick="saveOnly()">Save Settings</button>
        </div>
      </section>
    </div>

    <div>
      <section>
        <h2>Screenshot Parameters</h2>
        <div class="grid2">
          <div>
            <label title="输出图像类型。Scalar field 只输出当前 Field 的云图；Vector arrows 会在云图上叠加速度箭头，对应 ParaView 的 Glyph。">Visualization</label>
            <select id="visualizationType" title="输出图像类型。Scalar field 只输出当前 Field 的云图；Vector arrows 会在云图上叠加速度箭头，对应 ParaView 的 Glyph。">
              <option value="scalar">Scalar field</option>
              <option value="vector_arrows">Vector arrows over scalar</option>
            </select>
          </div>
          <div>
            <label title="输出图片长边像素数。程序会根据网格几何比例自动计算短边，不再靠截图后裁剪。ParaView 5.10 下建议 1400 左右。">Target long side</label>
            <input id="targetLongSide" type="number" min="64" title="输出图片长边像素数。程序会根据网格几何比例自动计算短边，不再靠截图后裁剪。ParaView 5.10 下建议 1400 左右。">
          </div>
        </div>
        <div id="vectorOptions" class="vector-options" hidden>
          <div class="grid2">
            <div>
              <label title="用于箭头方向的矢量场。OpenFOAM 速度场通常是 U。">Vector field</label>
              <select id="vectorField" title="用于箭头方向的矢量场。OpenFOAM 速度场通常是 U。"></select>
            </div>
            <div>
              <label title="箭头着色方式。Speed 表示按矢量模长着色；Black 表示统一黑色箭头。">Arrow color</label>
              <select id="arrowColor" title="箭头着色方式。Speed 表示按矢量模长着色；Black 表示统一黑色箭头。">
                <option value="speed">Speed</option>
                <option value="black">Black</option>
              </select>
            </div>
          </div>
          <div class="grid2">
            <div>
              <label title="箭头密度。Sparse 少量箭头，Medium 默认，Dense 箭头更多但渲染更慢。">Arrow density</label>
              <select id="arrowDensity" title="箭头密度。Sparse 少量箭头，Medium 默认，Dense 箭头更多但渲染更慢。">
                <option value="sparse">Sparse</option>
                <option value="medium">Medium</option>
                <option value="dense">Dense</option>
              </select>
            </div>
            <div>
              <label title="箭头长度。Auto 默认；Small 更短；Large 更长。这里控制显示长度，不改变原始速度数据。">Arrow scale</label>
              <select id="arrowScale" title="箭头长度。Auto 默认；Small 更短；Large 更长。这里控制显示长度，不改变原始速度数据。">
                <option value="auto">Auto</option>
                <option value="small">Small</option>
                <option value="large">Large</option>
              </select>
            </div>
          </div>
          <div class="grid3">
            <div>
              <label title="Glyph 源类型。默认 2D Glyph，适合二维切片；3D Arrow 会生成立体箭头。">Glyph source</label>
              <select id="glyphSource" title="Glyph 源类型。默认 2D Glyph，适合二维切片；3D Arrow 会生成立体箭头。">
                <option value="2d_glyph">2D Glyph</option>
                <option value="3d_arrow">3D Arrow</option>
              </select>
            </div>
            <div id="glyph2dShapeGroup">
              <label title="2D Glyph 的形状，对应 ParaView Glyph Source 2D 的 Glyph Type。">2D shape</label>
              <select id="glyph2dShape" title="2D Glyph 的形状，对应 ParaView Glyph Source 2D 的 Glyph Type。">
                <option value="Arrow">Arrow</option>
                <option value="ThickArrow">ThickArrow</option>
                <option value="HookedArrow">HookedArrow</option>
                <option value="EdgeArrow">EdgeArrow</option>
                <option value="Dash">Dash</option>
                <option value="Cross">Cross</option>
                <option value="Triangle">Triangle</option>
                <option value="Square">Square</option>
                <option value="Circle">Circle</option>
              </select>
            </div>
            <div>
              <label title="Glyph 的采样模式，对应 ParaView Glyph Mode。Uniform 适合批量截图；Every Nth Point 可按网格点间隔抽样。">Glyph mode</label>
              <select id="glyphMode" title="Glyph 的采样模式，对应 ParaView Glyph Mode。Uniform 适合批量截图；Every Nth Point 可按网格点间隔抽样。">
                <option value="Uniform Spatial Distribution (Bounds Based)">Uniform Bounds</option>
                <option value="Uniform Spatial Distribution (Surface Sampling)">Uniform Surface</option>
                <option value="Every Nth Point">Every Nth Point</option>
                <option value="All Points">All Points</option>
              </select>
            </div>
          </div>
          <div class="grid3">
            <div>
              <label title="Uniform 采样时最多生成多少个箭头，对应 Maximum Number Of Sample Points。">Max points</label>
              <input id="glyphMaxPoints" type="number" min="1" title="Uniform 采样时最多生成多少个箭头，对应 Maximum Number Of Sample Points。">
            </div>
            <div>
              <label title="Every Nth Point 采样时每隔多少个点放一个箭头，对应 Stride。">Glyph stride</label>
              <input id="glyphStride" type="number" min="1" title="Every Nth Point 采样时每隔多少个点放一个箭头，对应 Stride。">
            </div>
            <div>
              <label title="Glyph 线宽，主要影响 2D Glyph 的线条粗细。">Line width</label>
              <input id="glyphLineWidth" type="number" step="0.1" min="0.1" title="Glyph 线宽，主要影响 2D Glyph 的线条粗细。">
            </div>
          </div>
          <div class="grid3">
            <div>
              <label title="缩放数组，对应 ParaView Scale Array。None 表示所有箭头等长；Vector magnitude 表示按矢量模长缩放；Current field 表示按底图字段缩放。">Scale array</label>
              <select id="glyphScaleArray" title="缩放数组，对应 ParaView Scale Array。None 表示所有箭头等长；Vector magnitude 表示按矢量模长缩放；Current field 表示按底图字段缩放。">
                <option value="none">None</option>
                <option value="vector">Vector magnitude</option>
                <option value="field">Current field</option>
              </select>
            </div>
            <div>
              <label title="精确 Scale Factor。留空时使用 Arrow scale 的 Small/Auto/Large 自动值；填写数字时直接使用该值。">Scale factor</label>
              <input id="glyphScaleFactor" spellcheck="false" title="精确 Scale Factor。留空时使用 Arrow scale 的 Small/Auto/Large 自动值；填写数字时直接使用该值。">
            </div>
            <div>
              <label title="矢量缩放模式，对应 Vector Scale Mode。通常使用 Scale by Magnitude。">Vector scale mode</label>
              <select id="glyphVectorScaleMode" title="矢量缩放模式，对应 Vector Scale Mode。通常使用 Scale by Magnitude。">
                <option value="Scale by Magnitude">Scale by Magnitude</option>
                <option value="Scale by Components">Scale by Components</option>
              </select>
            </div>
          </div>
          <div id="glyph3dOptions" class="grid3">
            <div>
              <label title="3D Arrow 箭头头部长度，对应 Tip Length。">Tip length</label>
              <input id="glyphTipLength" type="number" step="0.01" min="0" title="3D Arrow 箭头头部长度，对应 Tip Length。">
            </div>
            <div>
              <label title="3D Arrow 箭头头部半径，对应 Tip Radius。">Tip radius</label>
              <input id="glyphTipRadius" type="number" step="0.01" min="0" title="3D Arrow 箭头头部半径，对应 Tip Radius。">
            </div>
            <div>
              <label title="3D Arrow 箭杆半径，对应 Shaft Radius。">Shaft radius</label>
              <input id="glyphShaftRadius" type="number" step="0.01" min="0" title="3D Arrow 箭杆半径，对应 Shaft Radius。">
            </div>
          </div>
          <div class="checkrow">
            <label title="启用后按 Vector field 自动旋转箭头方向，对应 Orientation Array。"><input id="glyphOrient" type="checkbox" title="启用后按 Vector field 自动旋转箭头方向，对应 Orientation Array。"> Orient by vector</label>
            <label title="启用后填充 2D Glyph 面片，对应 Glyph Source 2D 的 Filled。"><input id="glyph2dFilled" type="checkbox" title="启用后填充 2D Glyph 面片，对应 Glyph Source 2D 的 Filled。"> Filled 2D glyph</label>
          </div>
        </div>
        <div class="grid3">
          <div>
            <div class="label-help">
              <label>时间模式</label>
              <span class="help-tip" tabindex="0" aria-label="时间模式说明" data-tip="selected：使用下方指定的物理时间列表。
all：输出所有已保存的时间步。
stride：按固定间隔抽样输出。
range：只输出指定时间范围内的已保存时间步。">?</span>
            </div>
            <select id="timeMode" title="选择要截图的时间步来源：selected 使用指定时间列表；all 输出全部保存时间；stride 按间隔抽样；range 输出指定时间范围内的所有保存时间。">
              <option value="selected">selected</option>
              <option value="all">all</option>
              <option value="stride">stride</option>
              <option value="range">range</option>
            </select>
          </div>
          <div id="strideGroup" class="conditional">
            <label title="当 Time mode 为 stride 时生效。1 表示每个时间步都截，10 表示每 10 个保存时间步截一张。">Stride</label>
            <input id="timeStride" type="number" min="1" title="当 Time mode 为 stride 时生效。1 表示每个时间步都截，10 表示每 10 个保存时间步截一张。">
          </div>
        </div>
        <div id="selectedTimesGroup" class="conditional">
          <label title="当 Time mode 为 selected 时生效。填写要截图的物理时间，可用逗号、空格或换行分隔；程序会匹配到最接近的已保存时间步。">Selected times</label>
          <textarea id="selectedTimes" spellcheck="false" title="当 Time mode 为 selected 时生效。填写要截图的物理时间，可用逗号、空格或换行分隔；程序会匹配到最接近的已保存时间步。"></textarea>
        </div>
        <div id="timeRangeGroup" class="conditional">
          <label title="当 Time mode 为 range 时生效。填写起止时间，例如 0, 0.00699，只输出这个范围内已保存的时间步。">Time range</label>
          <input id="timeRange" spellcheck="false" title="当 Time mode 为 range 时生效。填写起止时间，例如 0, 0.00699，只输出这个范围内已保存的时间步。">
        </div>

        <div class="grid3">
          <div>
            <label title="用于云图着色的 OpenFOAM 场变量名称，例如 alpha.water、p、p_rgh、T、U。Detect 后会列出 case 中发现的字段。">Field</label>
            <select id="fieldName" title="用于云图着色的 OpenFOAM 场变量名称，例如 alpha.water、p、p_rgh、T、U。Detect 后会列出 case 中发现的字段。"></select>
          </div>
          <div>
            <label title="字段数据关联位置。OpenFOAM 体场通常读成 CELLS；如果某些数据在点上，再改为 POINTS。">Association</label>
            <select id="fieldAssociation" title="字段数据关联位置。OpenFOAM 体场通常读成 CELLS；如果某些数据在点上，再改为 POINTS。">
              <option value="CELLS">CELLS</option>
              <option value="POINTS">POINTS</option>
            </select>
          </div>
          <div>
            <label title="矢量变量的分量选择。U 这类矢量常用 Magnitude 表示模长；标量变量如 alpha.water、p 留空。">Component</label>
            <select id="fieldComponent" title="矢量变量的分量选择。U 这类矢量常用 Magnitude 表示模长；标量变量如 alpha.water、p 留空。"></select>
          </div>
        </div>
        <div class="grid3">
          <div>
            <div class="label-help">
              <label>色标范围模式</label>
              <span class="help-tip" tabindex="0" aria-label="色标范围模式说明" data-tip="State file range：使用 ParaView state 文件中保存的色标范围。
Data range：使用当前时间步的数据范围。
Custom data range：使用手动输入的最小值和最大值。
All times：使用所有已保存时间步统一计算出的范围。
Visible data range：使用当前视图中可见对象的数据范围。">?</span>
            </div>
            <select id="colorRangeMode" title="Color range mode follows ParaView naming: state file, data, custom, all times, or visible data.">
              <option value="state_file">State file range</option>
              <option value="data">Data range</option>
              <option value="custom">Custom data range</option>
              <option value="all_times">All times</option>
              <option value="visible">Visible data range</option>
            </select>
          </div>
          <div id="colorRangeGroup">
            <label title="Custom color range, for example 0, 100000. Only used by Custom data range.">Custom range</label>
            <input id="colorRange" spellcheck="false" title="Custom color range, for example 0, 100000. Only used by Custom data range.">
          </div>
          <div>
            <label title="ParaView OpenFOAMReader 中要读取的网格区域。普通单区域 case 通常是 internalMesh；多区域 case 可填写多个，用逗号分隔。">Mesh regions</label>
            <input id="meshRegions" spellcheck="false" title="ParaView OpenFOAMReader 中要读取的网格区域。普通单区域 case 通常是 internalMesh；多区域 case 可填写多个，用逗号分隔。">
          </div>
        </div>

        <div class="grid3">
          <div>
            <label title="切片模式。wedge_midplane 适合当前这种 wedge/轴对称 case，取 z=0 中面；radial 表示绕旋转轴生成径向切片；normal 表示直接把角度方向作为切片法向。">Slice mode</label>
            <select id="angleMode" title="切片模式。wedge_midplane 适合当前这种 wedge/轴对称 case，取 z=0 中面；radial 表示绕旋转轴生成径向切片；normal 表示直接把角度方向作为切片法向。">
              <option value="wedge_midplane">wedge_midplane</option>
              <option value="radial">radial</option>
              <option value="normal">normal</option>
            </select>
          </div>
          <div>
            <label title="旋转轴方向。当前 case 的轴向是 Y；完整三维旋转切片时，这个轴决定 angles deg 如何解释。">Rotation axis</label>
            <select id="rotationAxis" title="旋转轴方向。当前 case 的轴向是 Y；完整三维旋转切片时，这个轴决定 angles deg 如何解释。">
              <option value="X">X</option>
              <option value="Y">Y</option>
              <option value="Z">Z</option>
            </select>
          </div>
          <div>
            <label title="要输出的切片角度，单位为度，可填写多个，例如 0, 30, 60, 90。wedge_midplane 模式下通常只需要 0。">Angles deg</label>
            <input id="anglesDeg" spellcheck="false" title="要输出的切片角度，单位为度，可填写多个，例如 0, 30, 60, 90。wedge_midplane 模式下通常只需要 0。">
          </div>
        </div>
        <div class="grid3">
          <div>
            <label title="切片平面经过的点，格式为 x, y, z。对于以原点为轴/中心的 case 通常为 0, 0, 0。">Slice origin</label>
            <input id="sliceOrigin" spellcheck="false" title="切片平面经过的点，格式为 x, y, z。对于以原点为轴/中心的 case 通常为 0, 0, 0。">
          </div>
          <div>
            <label title="相机离切片平面的距离，只影响观察位置，不改变几何比例。当前采用平行投影，通常不需要频繁修改。">Camera distance</label>
            <input id="cameraDistance" type="number" step="0.01" title="相机离切片平面的距离，只影响观察位置，不改变几何比例。当前采用平行投影，通常不需要频繁修改。">
          </div>
          <div>
            <label title="网格边界外预留的空白比例。1.0 表示贴合几何包围盒；1.03 表示额外留 3% 边距。">Mesh padding</label>
            <input id="meshPadding" type="number" step="0.01" title="网格边界外预留的空白比例。1.0 表示贴合几何包围盒；1.03 表示额外留 3% 边距。">
          </div>
        </div>

      </section>
      <section>
        <h2>Log</h2>
        <pre id="log"></pre>
      </section>
    </div>
  </main>

  <script>
    let state = {};

    function $(id) { return document.getElementById(id); }
    function setBusy(busy, text) {
      document.querySelectorAll('button').forEach(btn => btn.disabled = busy);
      $('topStatus').textContent = text || (busy ? 'Working' : 'Ready');
    }
    function log(text) { $('log').textContent = text || ''; }
    function csv(value) { return Array.isArray(value) ? value.join(', ') : (value || ''); }

    function normalizeColorRangeMode(value) {
      const aliases = {
        fixed: 'custom',
        manual: 'custom',
        auto_each: 'data',
        auto_all: 'all_times',
        state: 'state_file',
        state_range: 'state_file'
      };
      const key = String(value || 'custom').trim().toLowerCase();
      return aliases[key] || key;
    }

    function parseNumberList(value) {
      if (Array.isArray(value)) return value.map(Number);
      return String(value || '')
        .split(/[,;\s]+/)
        .filter(Boolean)
        .map(Number);
    }

    function fmt(value) {
      if (!Number.isFinite(value)) return '-';
      return Number(value).toPrecision(6).replace(/\.?0+$/, '');
    }

    function meshBoundsText(bounds) {
      if (!bounds || !bounds.min || !bounds.max) return '-';
      return `x ${fmt(bounds.min[0])}..${fmt(bounds.max[0])}, y ${fmt(bounds.min[1])}..${fmt(bounds.max[1])}, z ${fmt(bounds.min[2])}..${fmt(bounds.max[2])}`;
    }

    function fieldClass(fieldName) {
      const detected = state.detected || {};
      const fieldTypes = detected.field_types || {};
      return String(fieldTypes[fieldName] || '').toLowerCase();
    }

    function componentChoices(fieldName, currentValue) {
      const cls = fieldClass(fieldName);
      let choices = [{ value: '', label: 'Default' }];
      if (cls.includes('vector') || fieldName === 'U') {
        choices = choices.concat([
          { value: 'Magnitude', label: 'Magnitude' },
          { value: 'X', label: 'X' },
          { value: 'Y', label: 'Y' },
          { value: 'Z', label: 'Z' },
        ]);
      } else if (cls.includes('tensor')) {
        choices = choices.concat([
          { value: 'Magnitude', label: 'Magnitude' },
          { value: 'XX', label: 'XX' },
          { value: 'XY', label: 'XY' },
          { value: 'XZ', label: 'XZ' },
          { value: 'YX', label: 'YX' },
          { value: 'YY', label: 'YY' },
          { value: 'YZ', label: 'YZ' },
          { value: 'ZX', label: 'ZX' },
          { value: 'ZY', label: 'ZY' },
          { value: 'ZZ', label: 'ZZ' },
        ]);
      }
      if (currentValue && !choices.some(choice => choice.value === currentValue)) {
        choices.push({ value: currentValue, label: currentValue });
      }
      return choices;
    }

    function updateComponentChoices(currentValue) {
      const select = $('fieldComponent');
      const selected = currentValue !== undefined ? currentValue : select.value;
      const choices = componentChoices($('fieldName').value, selected);
      select.innerHTML = '';
      choices.forEach(choice => {
        const option = document.createElement('option');
        option.value = choice.value;
        option.textContent = choice.label;
        select.appendChild(option);
      });
      select.value = choices.some(choice => choice.value === selected) ? selected : '';
    }

    function toggleBatchSection() {
      document.getElementById('batchSection').classList.toggle('collapsed');
    }

    function updateTimeInputs() {
      const mode = $('timeMode').value;
      $('selectedTimesGroup').hidden = mode !== 'selected';
      $('timeRangeGroup').hidden = mode !== 'range';
      $('strideGroup').hidden = mode !== 'stride';
    }

    function updateColorRangeOptions() {
      $('colorRangeGroup').hidden = $('colorRangeMode').value !== 'custom';
    }

    function hideControlGroup(id) {
      const element = $(id);
      if (!element) return;
      const group = element.closest('.grid2 > div, .grid3 > div') || element;
      group.hidden = true;
    }

    function hideLegacyStateControls() {
      [
        'visualizationType',
        'targetLongSide',
        'fieldName',
        'fieldAssociation',
        'fieldComponent',
        'meshRegions',
        'angleMode',
        'rotationAxis',
        'anglesDeg',
        'sliceOrigin',
        'cameraDistance',
        'meshPadding'
      ].forEach(hideControlGroup);

      const vectorOptions = $('vectorOptions');
      if (vectorOptions) vectorOptions.hidden = true;
    }

    function readSettings() {
      return {
        pvsm_state_file: $('pvsmStateFile').value,
        batch_enabled: $('batchEnabled').checked,
        batch_case_root: $('batchCaseRoot').value,
        output_root: $('outputRoot').value,
        time_mode: $('timeMode').value,
        selected_times: $('selectedTimes').value,
        time_stride: Number($('timeStride').value || 1),
        time_range: $('timeRange').value,
        color_range_mode: $('colorRangeMode').value,
        use_color_range: $('colorRangeMode').value === 'custom',
        color_range: $('colorRange').value,
      };
    }

    function render(next) {
      state = next;
      const config = next.config || {};
      const detected = next.detected || {};
      const settings = next.settings || {};
      const runOptions = next.run_options || {};
      const batchCases = next.batch_cases || [];

      $('caseDir').value = detected.case_dir || config.original_case_dir || config.case_dir || '';
      $('outputDir').value = config.output_dir || '';
      $('styleSource').value = 'pvsm';
      $('pvsmStateFile').value = runOptions.pvsm_state_file || config.pvsm_state_file || '';
      $('fieldPolicy').value = 'state';
      $('stateScalarFilterPolicy').value = 'warn';
      $('batchEnabled').checked = !!(runOptions.batch_enabled || config.batch_enabled);
      $('batchCaseRoot').value = runOptions.batch_case_root || config.batch_case_root || '';
      $('outputRoot').value = runOptions.output_root || config.output_root || config.output_dir || '';
      $('batchSummary').textContent = batchCases.length ? `${batchCases.length} prepared` : '-';
      $('pvpython').textContent = config.pvpython_path || '-';
      $('pvversion').textContent = config.paraview_version || '-';
      $('preparedCase').textContent = config.case_dir || '-';
      $('foamFile').textContent = config.case_file || (detected.foam_files || []).join(', ') || '-';
      $('timesSummary').textContent = detected.times && detected.times.length
        ? `${detected.times.length} (${detected.times[0]} .. ${detected.times[detected.times.length - 1]})`
        : '-';
      $('fieldsSummary').textContent = detected.fields && detected.fields.length ? detected.fields.join(', ') : '-';
      $('boundsSummary').textContent = meshBoundsText(detected.mesh_bounds);
      $('asciiSummary').textContent = detected.path_is_ascii ? 'yes' : 'no, will copy to ASCII work folder';

      $('timeMode').value = settings.time_mode || 'selected';
      $('selectedTimes').value = csv(settings.selected_times);
      $('timeStride').value = settings.time_stride || 10;
      $('timeRange').value = csv(settings.time_range);
      updateTimeInputs();
      $('colorRangeMode').value = normalizeColorRangeMode(settings.color_range_mode || (settings.use_color_range ? 'custom' : 'data'));
      $('colorRange').value = csv(settings.color_range);
      updateColorRangeOptions();
      updateStyleOptions();
      hideLegacyStateControls();
    }

    async function refresh() {
      setBusy(true, 'Loading');
      try {
        render(await window.pywebview.api.get_state());
      } catch (err) {
        log(String(err));
      } finally {
        setBusy(false, 'Ready');
      }
    }

    function updateVectorOptions() {
      const showGlyph = $('visualizationType').value === 'vector_arrows';
      $('vectorOptions').hidden = !showGlyph;
      if (!showGlyph) return;
      const is3d = $('glyphSource').value === '3d_arrow';
      $('glyph2dShapeGroup').hidden = is3d;
      $('glyph3dOptions').hidden = !is3d;
    }

    function updateStyleOptions() {
      $('pvsmOptions').hidden = false;
      $('batchSection').hidden = false;
      hideLegacyStateControls();
    }

    async function browseCase() {
      const picked = await window.pywebview.api.choose_case_dir();
      if (picked) $('caseDir').value = picked;
    }

    async function browseOutput() {
      const picked = await window.pywebview.api.choose_output_dir();
      if (picked) $('outputDir').value = picked;
    }

    async function browsePvsm() {
      const picked = await window.pywebview.api.choose_pvsm_file();
      if (picked) $('pvsmStateFile').value = picked;
    }

    async function browseBatchRoot() {
      const picked = await window.pywebview.api.choose_batch_root();
      if (picked) $('batchCaseRoot').value = picked;
    }

    async function browseOutputRoot() {
      const picked = await window.pywebview.api.choose_output_dir();
      if (picked) $('outputRoot').value = picked;
    }

    async function prepareBatch() {
      setBusy(true, 'Preparing batch');
      try {
        await window.pywebview.api.save_settings(readSettings());
        const result = await window.pywebview.api.prepare_batch_cases($('batchCaseRoot').value, $('outputRoot').value);
        if (result.state) render(result.state);
        log((result.stdout || '') + (result.stderr || ''));
        $('topStatus').textContent = result.ok ? 'Batch prepared' : 'Batch prepare failed';
        $('topStatus').className = result.ok ? 'status ok' : 'status bad';
      } catch (err) {
        log(String(err));
        $('topStatus').textContent = 'Batch prepare failed';
        $('topStatus').className = 'status bad';
      } finally {
        setBusy(false);
      }
    }

    async function prepare() {
      setBusy(true, 'Preparing');
      try {
        const result = await window.pywebview.api.prepare_environment($('caseDir').value, $('outputDir').value);
        render(result.state);
        log((result.stdout || '') + (result.stderr || ''));
        $('topStatus').textContent = result.ok ? 'Prepared' : 'Prepare failed';
        $('topStatus').className = result.ok ? 'status ok' : 'status bad';
      } catch (err) {
        log(String(err));
        $('topStatus').textContent = 'Prepare failed';
        $('topStatus').className = 'status bad';
      } finally {
        setBusy(false);
      }
    }

    async function saveOnly() {
      setBusy(true, 'Saving');
      try {
        await window.pywebview.api.save_settings(readSettings());
        $('topStatus').textContent = 'Settings saved';
        $('topStatus').className = 'status ok';
      } catch (err) {
        log(String(err));
        $('topStatus').textContent = 'Save failed';
        $('topStatus').className = 'status bad';
      } finally {
        setBusy(false);
      }
    }

    async function runShots() {
      setBusy(true, 'Running');
      try {
        const result = await window.pywebview.api.run_screenshots(
          readSettings(),
          $('caseDir').value,
          $('outputDir').value
        );
        if (result.state) render(result.state);
        log((result.stdout || '') + (result.stderr || ''));
        $('topStatus').textContent = result.ok ? 'Screenshots complete' : 'Run failed';
        $('topStatus').className = result.ok ? 'status ok' : 'status bad';
      } catch (err) {
        log(String(err));
        $('topStatus').textContent = 'Run failed';
        $('topStatus').className = 'status bad';
      } finally {
        setBusy(false);
      }
    }

    async function openOutput() {
      const result = await window.pywebview.api.open_output_dir();
      if (!result.ok) log(result.error || 'Could not open output folder');
    }

    window.addEventListener('pywebviewready', refresh);
    window.addEventListener('DOMContentLoaded', () => {
      $('colorRangeMode').addEventListener('change', updateColorRangeOptions);
      $('timeMode').addEventListener('change', updateTimeInputs);
      hideLegacyStateControls();
    });
  </script>
</body>
</html>
"""


def main():
    api = PvshotApi()
    webview.create_window("PvShot", html=HTML, js_api=api, width=1180, height=840)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
