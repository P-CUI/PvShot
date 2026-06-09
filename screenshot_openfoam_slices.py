"""
Batch screenshots for the OpenFOAM case in:
    D:\\Projects2026\\paraview screenshot workspace\\example\\1000cSt+33

Run with ParaView's Python:
    pvpython screenshot_openfoam_slices.py

This case is a wedge/axisymmetric OpenFOAM case. Its useful 2-D section is the
mid-plane z=0, with x as radius and y as axis direction. For a true full-3D case,
change ANGLE_MODE to "radial" and set ROTATION_AXIS as needed.
"""

import json
import re
import traceback
import xml.etree.ElementTree as ET
from math import cos, radians, sin
from pathlib import Path

from paraview import servermanager
from paraview.simple import *  # noqa: F401,F403


# =========================
# CONFIG: edit these values
# =========================

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "pvshot_config.json"


def load_runtime_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError(
            "Missing pvshot_config.json. Run prepare_pvshot_environment.ps1 "
            "or run run_screenshots.bat before starting pvpython directly."
        )

    with CONFIG_FILE.open("r", encoding="utf-8-sig") as handle:
        config = json.load(handle)

    required = ["pvsm_state_file"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(
            "pvshot_config.json is missing required keys: " + ", ".join(missing)
        )

    return config


RUNTIME_CONFIG = load_runtime_config()
SCREENSHOT_SETTINGS = RUNTIME_CONFIG.get("screenshot_settings", {})
STYLE_SOURCE = "pvsm"
PVSM_STATE_FILE = Path(RUNTIME_CONFIG.get("pvsm_state_file", ""))
FIELD_POLICY = "override"
STATE_SCALAR_FILTER_POLICY = "warn"
BATCH_CASES = list(RUNTIME_CONFIG.get("batch_cases") or [])
OUTPUT_ROOT = Path(RUNTIME_CONFIG.get("output_root") or RUNTIME_CONFIG.get("output_dir", ""))
CASE_DIR = Path(RUNTIME_CONFIG.get("case_dir", "."))
CASE_FILE = Path(RUNTIME_CONFIG.get("case_file", ""))
OUTPUT_DIR = Path(RUNTIME_CONFIG.get("output_dir", OUTPUT_ROOT or "."))


def config_value(name, default):
    return SCREENSHOT_SETTINGS.get(name, default)


def config_bool(name, default):
    value = config_value(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def config_float_list(name, default, count=None):
    value = config_value(name, default)
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    else:
        parts = list(value)

    result = [float(part) for part in parts]
    if count is not None and len(result) != count:
        raise ValueError(f"{name} must contain {count} values")
    return result


def config_string_list(name, default):
    value = config_value(name, default)
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    else:
        parts = [str(part).strip() for part in value if str(part).strip()]
    return parts or None


def normalize_color_range_mode(value):
    aliases = {
        "fixed": "custom",
        "manual": "custom",
        "manual_range": "custom",
        "auto_each": "data",
        "single-frame": "data",
        "single_frame": "data",
        "single_frame_auto": "data",
        "auto_all": "all_times",
        "unified": "all_times",
        "unified_auto": "all_times",
        "state": "state_file",
        "state_range": "state_file",
        "pvsm": "state_file",
    }
    mode = str(value or "custom").strip().lower()
    return aliases.get(mode, mode)


def configured_state_color_field(default="p"):
    try:
        root = ET.parse(str(PVSM_STATE_FILE)).getroot()
    except Exception:
        return default
    for prop in root.iter():
        if prop.tag != "Property" or prop.attrib.get("name") != "ColorArrayName":
            continue
        values = [
            element.attrib.get("value", "")
            for element in prop
            if element.tag == "Element" and element.attrib.get("value", "")
        ]
        for value in reversed(values):
            if value and value not in ("0", "1"):
                return value
    return default

# Time selection:
#   "selected" - use SELECTED_TIMES, snapped to the nearest available time.
#   "all"      - capture every saved OpenFOAM time folder.
#   "stride"   - capture every TIME_STRIDE-th saved time.
#   "range"    - capture saved times inside [TIME_RANGE[0], TIME_RANGE[1]].
TIME_MODE = str(config_value("time_mode", "selected"))
SELECTED_TIMES = config_float_list(
    "selected_times", [0.0, 0.0015, 0.003, 0.0045, 0.006, 0.00699]
)
TIME_STRIDE = int(config_value("time_stride", 10))
TIME_RANGE = config_float_list("time_range", [0.0, 0.00699], count=2)

# The GUI no longer exposes field override; internally we reapply the field
# stored in the state file to preserve the old no-background rendering path.
FIELD_NAME = str(config_value("field_name", configured_state_color_field()))
FIELD_ASSOCIATION = str(config_value("field_association", "CELLS"))
FIELD_COMPONENT = config_value("field_component", None)
if FIELD_COMPONENT == "":
    FIELD_COMPONENT = None
VISUALIZATION_TYPE = str(config_value("visualization_type", "scalar")).lower()
VECTOR_FIELD = str(config_value("vector_field", "U"))
ARROW_DENSITY = str(config_value("arrow_density", "medium")).lower()
ARROW_SCALE = str(config_value("arrow_scale", "auto")).lower()
ARROW_COLOR = str(config_value("arrow_color", "speed")).lower()
GLYPH_SOURCE = str(config_value("glyph_source", "2d_glyph")).lower()
GLYPH_2D_SHAPE = str(config_value("glyph_2d_shape", "Arrow"))
GLYPH_2D_FILLED = config_bool("glyph_2d_filled", True)
GLYPH_MODE = str(config_value("glyph_mode", "Uniform Spatial Distribution (Bounds Based)"))
GLYPH_MAX_POINTS = int(config_value("glyph_max_points", 750))
GLYPH_STRIDE = int(config_value("glyph_stride", 10))
GLYPH_ORIENT = config_bool("glyph_orient", True)
GLYPH_SCALE_ARRAY = str(config_value("glyph_scale_array", "none")).lower()
GLYPH_SCALE_FACTOR = config_value("glyph_scale_factor", "")
GLYPH_VECTOR_SCALE_MODE = str(config_value("glyph_vector_scale_mode", "Scale by Magnitude"))
GLYPH_LINE_WIDTH = float(config_value("glyph_line_width", 1.5))
GLYPH_TIP_LENGTH = float(config_value("glyph_tip_length", 0.35))
GLYPH_TIP_RADIUS = float(config_value("glyph_tip_radius", 0.1))
GLYPH_SHAFT_RADIUS = float(config_value("glyph_shaft_radius", 0.03))
COLOR_RANGE_MODE = normalize_color_range_mode(
    config_value("color_range_mode", "custom" if config_bool("use_color_range", True) else "data")
)
COLOR_RANGE = config_float_list("color_range", [0.0, 1.0], count=2)
VALID_COLOR_RANGE_MODES = ("state_file", "data", "custom", "all_times", "visible")
if COLOR_RANGE_MODE not in VALID_COLOR_RANGE_MODES:
    raise ValueError(
        "color_range_mode must be one of: " + ", ".join(VALID_COLOR_RANGE_MODES)
    )

# ParaView's OpenFOAM reader normally exposes this region for this case.
MESH_REGIONS = config_string_list("mesh_regions", ["internalMesh"])

# Slice settings.
# For this wedge case, "wedge_midplane" gives the physical x-y section at z=0.
# For full 3-D data, use "radial" to create angle-dependent radial planes.
ANGLE_MODE = str(config_value("angle_mode", "wedge_midplane"))
ANGLES_DEG = config_float_list("angles_deg", [0.0])
SLICE_ORIGIN = config_float_list("slice_origin", [0.0, 0.0, 0.0], count=3)
ROTATION_AXIS = str(config_value("rotation_axis", "Y"))

# Optional coordinate clip before slicing. The geometry is about:
#   x: 0..0.13889, y: 0..0.27778, z: -0.00139..0.00139
ROI_MODE = "full"
USE_BOX_CLIP = False
BOX_POSITION = config_float_list("box_position", [0.0, 0.0, -0.002], count=3)
BOX_LENGTH = config_float_list("box_length", [0.14, 0.28, 0.004], count=3)

# Image/view settings. Image dimensions are derived from the slice geometry, so
# screenshots are framed by the mesh bounds instead of cropped after rendering.
TARGET_LONG_SIDE = 1400
MIN_IMAGE_SIDE = int(config_value("min_image_side", 64))
MAX_IMAGE_SIDE = int(config_value("max_image_side", 4096))
BACKGROUND = config_float_list("background", [1.0, 1.0, 1.0], count=3)
SHOW_MESH_EDGES = config_bool("show_mesh_edges", False)
SHOW_COLOR_BAR = config_bool("show_color_bar", False)
SHOW_ORIENTATION_AXES = False
AUTO_FRAME_MESH = config_bool("auto_frame_mesh", True)
MESH_PADDING = 1.0
CAMERA_DISTANCE = float(config_value("camera_distance", 0.45))


def as_path(path_value):
    return Path(path_value).expanduser().resolve()


def safe_path_part(text):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text).strip())
    cleaned = cleaned.strip(" .")
    return cleaned or "field"


def radial_direction(angle_deg, axis):
    a = radians(angle_deg)
    if axis.upper() == "Z":
        return [cos(a), sin(a), 0.0]
    if axis.upper() == "Y":
        return [cos(a), 0.0, sin(a)]
    if axis.upper() == "X":
        return [0.0, cos(a), sin(a)]
    raise ValueError("ROTATION_AXIS must be X, Y, or Z")


def radial_plane_normal(angle_deg, axis):
    """Normal for a radial plane that contains the rotation axis."""
    a = radians(angle_deg)
    if axis.upper() == "Z":
        return [-sin(a), cos(a), 0.0]
    if axis.upper() == "Y":
        return [-sin(a), 0.0, cos(a)]
    if axis.upper() == "X":
        return [0.0, -sin(a), cos(a)]
    raise ValueError("ROTATION_AXIS must be X, Y, or Z")


def vector_add(a, b):
    return [a[i] + b[i] for i in range(3)]


def vector_scale(v, scale):
    return [v[i] * scale for i in range(3)]


def vector_dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def vector_cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def vector_length(v):
    return max(vector_dot(v, v) ** 0.5, 1e-30)


def vector_normalize(v):
    length = vector_length(v)
    return [v[i] / length for i in range(3)]


def up_vector(axis):
    if axis.upper() == "Z":
        return [0.0, 0.0, 1.0]
    if axis.upper() == "Y":
        return [0.0, 1.0, 0.0]
    return [1.0, 0.0, 0.0]


def set_if_property(proxy, property_name, value):
    if property_name in proxy.ListProperties():
        setattr(proxy, property_name, value)
        return True
    return False


def configure_openfoam_reader(case_file):
    reader = OpenFOAMReader(FileName=str(case_file))
    reader.UpdatePipelineInformation()

    if MESH_REGIONS:
        set_if_property(reader, "MeshRegions", MESH_REGIONS)

    cell_arrays = [FIELD_NAME]
    point_arrays = [FIELD_NAME]
    if VISUALIZATION_TYPE == "vector_arrows" and VECTOR_FIELD not in cell_arrays:
        cell_arrays.append(VECTOR_FIELD)

    if FIELD_ASSOCIATION.upper() == "CELLS":
        set_if_property(reader, "CellArrays", cell_arrays)
    elif FIELD_ASSOCIATION.upper() == "POINTS":
        set_if_property(reader, "PointArrays", point_arrays)
        if VISUALIZATION_TYPE == "vector_arrows":
            set_if_property(reader, "CellArrays", [VECTOR_FIELD])

    reader.UpdatePipelineInformation()
    return reader


def available_times(reader, case_dir=CASE_DIR):
    values = list(getattr(reader, "TimestepValues", []) or [])
    if values:
        return [float(v) for v in values]

    # Fallback for unusual reader versions.
    times = []
    for child in Path(case_dir).iterdir():
        if child.is_dir():
            try:
                times.append(float(child.name))
            except ValueError:
                pass
    return sorted(times)


def nearest_time(target, times):
    return min(times, key=lambda value: abs(value - target))


def selected_times(reader, case_dir=CASE_DIR):
    times = available_times(reader, case_dir)
    if not times:
        raise RuntimeError("No saved OpenFOAM time values were found.")

    if TIME_MODE == "all":
        return times
    if TIME_MODE == "stride":
        return times[:: max(1, int(TIME_STRIDE))]
    if TIME_MODE == "range":
        lo, hi = TIME_RANGE
        return [t for t in times if lo <= t <= hi]
    if TIME_MODE == "selected":
        snapped = []
        for value in SELECTED_TIMES:
            actual = nearest_time(float(value), times)
            if actual not in snapped:
                snapped.append(actual)
        return snapped

    raise ValueError('TIME_MODE must be "selected", "all", "stride", or "range"')


def maybe_clip(source):
    if not USE_BOX_CLIP:
        return source

    clip = Clip(Input=source)
    clip.ClipType = "Box"
    clip.ClipType.Position = BOX_POSITION
    clip.ClipType.Length = BOX_LENGTH
    clip.Invert = 0
    return clip


def roi_bounds():
    return [
        BOX_POSITION[0],
        BOX_POSITION[0] + BOX_LENGTH[0],
        BOX_POSITION[1],
        BOX_POSITION[1] + BOX_LENGTH[1],
        BOX_POSITION[2],
        BOX_POSITION[2] + BOX_LENGTH[2],
    ]


def build_slice(source, angle_deg):
    if ANGLE_MODE == "wedge_midplane":
        # The OpenFOAM mesh is a narrow wedge around z=0. This extracts the
        # physical axisymmetric x-y section without depending on ParaView's
        # wedge rendering details.
        normal = [0.0, 0.0, 1.0]
    elif ANGLE_MODE == "radial":
        normal = radial_plane_normal(angle_deg, ROTATION_AXIS)
    elif ANGLE_MODE == "normal":
        normal = radial_direction(angle_deg, ROTATION_AXIS)
    else:
        raise ValueError('ANGLE_MODE must be "wedge_midplane", "radial", or "normal"')

    slice_filter = Slice(Input=source)
    slice_filter.SliceType = "Plane"
    slice_filter.SliceType.Origin = SLICE_ORIGIN
    slice_filter.SliceType.Normal = normal
    return slice_filter, normal


def configure_display(slice_filter, view, shared_color_range=None, single_frame_range=None):
    display = Show(slice_filter, view)
    display.Representation = "Surface With Edges" if SHOW_MESH_EDGES else "Surface"

    component = FIELD_COMPONENT if FIELD_COMPONENT else None
    if component:
        ColorBy(display, (FIELD_ASSOCIATION.upper(), FIELD_NAME, component))
    else:
        ColorBy(display, (FIELD_ASSOCIATION.upper(), FIELD_NAME))

    if COLOR_RANGE_MODE in ("data", "visible"):
        value_range = single_frame_range or range_for_slice(slice_filter)
        if value_range:
            rescale_transfer_functions(FIELD_NAME, value_range, display)
        else:
            rescale_display_color(display, shared_color_range)
    else:
        rescale_display_color(display, shared_color_range)
    display.SetScalarBarVisibility(view, SHOW_COLOR_BAR)
    if SHOW_COLOR_BAR:
        lut = GetColorTransferFunction(FIELD_NAME)
        scalar_bar = GetScalarBar(lut, view)
        scalar_bar.Title = FIELD_NAME
        scalar_bar.ComponentTitle = FIELD_COMPONENT if FIELD_COMPONENT else ""
    return display


def normalized_range(range_values):
    lower = float(range_values[0])
    upper = float(range_values[1])
    if lower == upper:
        delta = abs(lower) * 1e-6 or 1e-12
        lower -= delta
        upper += delta
    return [lower, upper]


def format_color_range(range_values):
    if not range_values:
        return "[]"
    return f"[{float(range_values[0]):g}, {float(range_values[1]):g}]"


def set_lut_auto_rescale_mode(lut, mode):
    if lut is None:
        return
    for value in (mode, -1 if mode == "Never" else mode):
        try:
            if set_if_property(lut, "AutomaticRescaleRangeMode", value):
                return
        except Exception:
            continue


def active_lookup_table(display, field_name):
    if display is not None:
        try:
            lut = getattr(display, "LookupTable", None)
            if lut:
                return lut
        except Exception:
            pass
    if field_name:
        return GetColorTransferFunction(field_name)
    return None


def active_opacity_function(display, field_name, lut=None):
    for proxy in (lut, display):
        if proxy is None:
            continue
        for property_name in ("ScalarOpacityFunction", "OpacityTransferFunction"):
            try:
                opacity = getattr(proxy, property_name, None)
                if opacity and hasattr(opacity, "RescaleTransferFunction"):
                    return opacity
            except Exception:
                pass
    if field_name:
        try:
            return GetOpacityTransferFunction(field_name)
        except Exception:
            return None
    return None


def rescale_transfer_functions(field_name, range_values, display=None):
    if not field_name:
        return
    lower, upper = normalized_range(range_values)
    lut = active_lookup_table(display, field_name)
    if lut is not None:
        set_lut_auto_rescale_mode(lut, "Never")
        lut.RescaleTransferFunction(lower, upper)
    try:
        opacity = active_opacity_function(display, field_name, lut)
        if opacity is not None:
            opacity.RescaleTransferFunction(lower, upper)
    except Exception:
        pass
    return [lower, upper]


def rescale_display_color_for_field(display, field_name, shared_color_range=None):
    if COLOR_RANGE_MODE == "custom":
        return rescale_transfer_functions(field_name, COLOR_RANGE, display)
    elif COLOR_RANGE_MODE == "all_times":
        if not shared_color_range:
            raise RuntimeError("Color range mode all_times requires a shared color range")
        return rescale_transfer_functions(field_name, shared_color_range, display)
    elif COLOR_RANGE_MODE == "state_file":
        return None
    else:
        if display is not None:
            display.RescaleTransferFunctionToDataRange(True, False)
        return None


def rescale_display_color(display, shared_color_range=None):
    rescale_display_color_for_field(display, FIELD_NAME, shared_color_range)


def density_to_points():
    if GLYPH_MAX_POINTS > 0:
        return GLYPH_MAX_POINTS
    mapping = {
        "sparse": 250,
        "medium": 750,
        "dense": 1800,
    }
    return mapping.get(ARROW_DENSITY, mapping["medium"])


def arrow_scale_factor(bounds):
    if str(GLYPH_SCALE_FACTOR).strip():
        return float(GLYPH_SCALE_FACTOR)

    width = max(bounds[1] - bounds[0], 1e-12)
    height = max(bounds[3] - bounds[2], 1e-12)
    diag = (width * width + height * height) ** 0.5
    mapping = {
        "small": 0.025,
        "auto": 0.04,
        "medium": 0.04,
        "large": 0.065,
    }
    return diag * mapping.get(ARROW_SCALE, mapping["auto"])


def glyph_source_name():
    if GLYPH_SOURCE in ("3d_arrow", "arrow", "3d"):
        return "Arrow"
    return "2D Glyph"


def configure_glyph_source(glyph):
    if GLYPH_SOURCE in ("3d_arrow", "arrow", "3d"):
        set_if_property(glyph.GlyphType, "TipLength", GLYPH_TIP_LENGTH)
        set_if_property(glyph.GlyphType, "TipRadius", GLYPH_TIP_RADIUS)
        set_if_property(glyph.GlyphType, "ShaftRadius", GLYPH_SHAFT_RADIUS)
        return

    set_if_property(glyph.GlyphType, "GlyphType", GLYPH_2D_SHAPE)
    set_if_property(glyph.GlyphType, "Filled", 1 if GLYPH_2D_FILLED else 0)


def configure_glyph_sampling(glyph):
    glyph.GlyphMode = GLYPH_MODE
    if GLYPH_MODE == "Every Nth Point":
        glyph.Stride = max(1, GLYPH_STRIDE)
    elif GLYPH_MODE.startswith("Uniform Spatial Distribution"):
        glyph.MaximumNumberOfSamplePoints = density_to_points()


def configure_glyph_scale(glyph, bounds):
    if GLYPH_SCALE_ARRAY == "vector":
        glyph.ScaleArray = ["POINTS", VECTOR_FIELD]
        glyph.VectorScaleMode = GLYPH_VECTOR_SCALE_MODE
    elif GLYPH_SCALE_ARRAY == "field":
        glyph.ScaleArray = ["POINTS", FIELD_NAME]
    else:
        glyph.ScaleArray = ["POINTS", "No scale array"]
    glyph.ScaleFactor = arrow_scale_factor(bounds)


def build_vector_arrows(slice_filter, view):
    point_data = CellDatatoPointData(Input=slice_filter)
    point_data.UpdatePipeline()

    glyph = Glyph(Input=point_data, GlyphType=glyph_source_name())
    configure_glyph_source(glyph)
    glyph.OrientationArray = (
        ["POINTS", VECTOR_FIELD] if GLYPH_ORIENT else ["POINTS", "No orientation array"]
    )
    configure_glyph_sampling(glyph)
    configure_glyph_scale(glyph, slice_filter.GetDataInformation().GetBounds())
    glyph.UpdatePipeline()

    display = Show(glyph, view)
    display.Representation = "Surface"
    set_if_property(display, "LineWidth", GLYPH_LINE_WIDTH)

    if ARROW_COLOR == "speed":
        ColorBy(display, ("POINTS", VECTOR_FIELD, "Magnitude"))
        display.RescaleTransferFunctionToDataRange(True, False)
        display.SetScalarBarVisibility(view, SHOW_COLOR_BAR)
    else:
        display.DiffuseColor = [0.0, 0.0, 0.0]
        display.SetScalarBarVisibility(view, False)

    return point_data, glyph, display


def component_index(array, component=None):
    if component is None:
        component = FIELD_COMPONENT
    if not component or array.GetNumberOfComponents() == 1:
        return 0

    if isinstance(component, str):
        text = component.strip().lower()
        if text == "magnitude":
            return -1
        if text in ("x", "0"):
            return 0
        if text in ("y", "1"):
            return 1
        if text in ("z", "2"):
            return 2

    return int(component)


def array_range_from_dataset(dataset, field_name=FIELD_NAME, association=FIELD_ASSOCIATION, component=FIELD_COMPONENT):
    candidates = []
    if association and str(association).upper() == "CELLS":
        candidates = [dataset.GetCellData()]
    elif association and str(association).upper() == "POINTS":
        candidates = [dataset.GetPointData()]
    else:
        candidates = [dataset.GetCellData(), dataset.GetPointData()]

    array = None
    for attributes in candidates:
        if not attributes:
            continue
        array = attributes.GetArray(field_name)
        if array:
            break
    if not array:
        return None

    return array.GetRange(component_index(array, component))


def collect_ranges(data_object, field_name=FIELD_NAME, association=FIELD_ASSOCIATION, component=FIELD_COMPONENT):
    ranges = []
    if data_object is None:
        return ranges

    if data_object.IsA("vtkCompositeDataSet"):
        iterator = data_object.NewIterator()
        iterator.SkipEmptyNodesOn()
        iterator.InitTraversal()
        while not iterator.IsDoneWithTraversal():
            dataset = iterator.GetCurrentDataObject()
            value_range = array_range_from_dataset(dataset, field_name, association, component)
            if value_range:
                ranges.append(value_range)
            iterator.GoToNextItem()
        iterator.UnRegister(None)
    else:
        value_range = array_range_from_dataset(data_object, field_name, association, component)
        if value_range:
            ranges.append(value_range)

    return ranges


def range_for_slice(slice_filter):
    fetched = servermanager.Fetch(slice_filter)
    ranges = collect_ranges(fetched)
    if not ranges:
        return None
    return [min(item[0] for item in ranges), max(item[1] for item in ranges)]


def compute_shared_color_range(source, times):
    combined = []
    for time_value in times:
        for angle_deg in ANGLES_DEG:
            slice_filter, _normal = build_slice(source, angle_deg)
            slice_filter.UpdatePipeline(time_value)
            value_range = range_for_slice(slice_filter)
            if value_range:
                combined.append(value_range)
            Delete(slice_filter)

    if not combined:
        raise RuntimeError(
            f"Could not compute shared color range for field {FIELD_NAME}"
        )

    return [min(item[0] for item in combined), max(item[1] for item in combined)]


def bounds_center(bounds):
    return [
        0.5 * (bounds[0] + bounds[1]),
        0.5 * (bounds[2] + bounds[3]),
        0.5 * (bounds[4] + bounds[5]),
    ]


def bounds_corners(bounds):
    xs = [bounds[0], bounds[1]]
    ys = [bounds[2], bounds[3]]
    zs = [bounds[4], bounds[5]]
    return [[x, y, z] for x in xs for y in ys for z in zs]


def projected_bounds(bounds, right, up):
    center = bounds_center(bounds)
    points = bounds_corners(bounds)
    right_values = [vector_dot(vector_add(p, vector_scale(center, -1.0)), right) for p in points]
    up_values = [vector_dot(vector_add(p, vector_scale(center, -1.0)), up) for p in points]
    return (
        max(right_values) - min(right_values),
        max(up_values) - min(up_values),
        center,
    )


def image_size_from_bounds(width, height):
    width = max(width, 1e-12)
    height = max(height, 1e-12)

    if width >= height:
        image_width = TARGET_LONG_SIDE
        image_height = round(TARGET_LONG_SIDE * height / width)
    else:
        image_height = TARGET_LONG_SIDE
        image_width = round(TARGET_LONG_SIDE * width / height)

    image_width = max(MIN_IMAGE_SIDE, min(MAX_IMAGE_SIDE, int(image_width)))
    image_height = max(MIN_IMAGE_SIDE, min(MAX_IMAGE_SIDE, int(image_height)))
    return [image_width, image_height]


def fit_view_size_to_aspect(view, desired_size, target_aspect):
    view.ViewSize = desired_size
    accepted_size = [int(view.ViewSize[0]), int(view.ViewSize[1])]
    max_width = max(MIN_IMAGE_SIDE, accepted_size[0])
    max_height = max(MIN_IMAGE_SIDE, accepted_size[1])

    if float(max_width) / float(max_height) >= target_aspect:
        image_height = max_height
        image_width = round(image_height * target_aspect)
    else:
        image_width = max_width
        image_height = round(image_width / target_aspect)

    image_width = max(MIN_IMAGE_SIDE, min(MAX_IMAGE_SIDE, int(image_width)))
    image_height = max(MIN_IMAGE_SIDE, min(MAX_IMAGE_SIDE, int(image_height)))
    image_size = [image_width, image_height]
    view.ViewSize = image_size
    return [int(view.ViewSize[0]), int(view.ViewSize[1])]


def configure_camera(view, slice_filter, normal):
    normal = vector_normalize(normal)
    up = vector_normalize(up_vector(ROTATION_AXIS))
    right = vector_normalize(vector_cross(up, normal))

    if ROI_MODE in ("camera_box", "zoom_box", "view_box"):
        bounds = roi_bounds()
    else:
        bounds = slice_filter.GetDataInformation().GetBounds()
    width, height, center = projected_bounds(bounds, right, up)
    target_aspect = width / max(height, 1e-12)
    desired_size = image_size_from_bounds(width, height)
    image_size = fit_view_size_to_aspect(view, desired_size, target_aspect)
    image_aspect = float(image_size[0]) / float(image_size[1])

    view.CameraFocalPoint = center
    view.CameraPosition = vector_add(center, vector_scale(normal, CAMERA_DISTANCE))
    view.CameraViewUp = up
    set_if_property(view, "CameraParallelProjection", 1)

    if AUTO_FRAME_MESH:
        half_height = max(height * 0.5, (width * 0.5) / image_aspect, 1e-9)
        set_if_property(view, "CameraParallelScale", half_height * MESH_PADDING)
    else:
        view.ResetCamera(False)

    return image_size


def time_token(time_value):
    token = f"{time_value:.9f}".rstrip("0").rstrip(".")
    if token == "-0":
        token = "0"
    return token.replace("-", "m").replace(".", "p")


def screenshot_name(case_file, time_index, time_value, angle_index, angle_deg):
    component = FIELD_COMPONENT if FIELD_COMPONENT else "scalar"
    visual_suffix = ""
    if VISUALIZATION_TYPE == "vector_arrows":
        glyph_label = "glyph3d" if GLYPH_SOURCE in ("3d_arrow", "arrow", "3d") else "glyph2d"
        visual_suffix = f"_vector_arrows_{glyph_label}_{safe_path_part(VECTOR_FIELD)}_{ARROW_COLOR}"
    roi_suffix = "" if ROI_MODE == "full" else f"_roi_{safe_path_part(ROI_MODE)}"
    return (
        f"{case_file.stem}_frame{time_index:04d}_"
        f"t{time_token(time_value)}_"
        f"{safe_path_part(FIELD_NAME)}_{safe_path_part(component)}{visual_suffix}_"
        f"{ANGLE_MODE}{roi_suffix}_angle{angle_index:03d}_{angle_deg:g}deg.png"
    )


def state_screenshot_name(case_name, time_index, time_value, field_label):
    return (
        f"{safe_path_part(case_name)}_frame{time_index:04d}_"
        f"t{time_token(time_value)}_"
        f"{safe_path_part(field_label)}_state.png"
    )


def view_image_size_from_current_aspect(view):
    view_size = [int(view.ViewSize[0]), int(view.ViewSize[1])]
    width = max(view_size[0], 1)
    height = max(view_size[1], 1)
    aspect = float(width) / float(height)
    if width >= height:
        desired = [TARGET_LONG_SIDE, round(TARGET_LONG_SIDE / aspect)]
    else:
        desired = [round(TARGET_LONG_SIDE * aspect), TARGET_LONG_SIDE]
    return fit_view_size_to_aspect(view, desired, aspect)


def valid_bounds(bounds):
    if not bounds or len(bounds) != 6:
        return False
    for low, high in ((bounds[0], bounds[1]), (bounds[2], bounds[3]), (bounds[4], bounds[5])):
        if low > high or abs(low) > 1e100 or abs(high) > 1e100:
            return False
    return True


def merge_bounds(bounds_items):
    valid_items = [item for item in bounds_items if valid_bounds(item)]
    if not valid_items:
        return None
    return [
        min(item[0] for item in valid_items),
        max(item[1] for item in valid_items),
        min(item[2] for item in valid_items),
        max(item[3] for item in valid_items),
        min(item[4] for item in valid_items),
        max(item[5] for item in valid_items),
    ]


def state_visible_bounds(view, time_value, fallback_source=None):
    bounds_items = []
    for display in visible_representations(view):
        if getattr(display, "Visibility", 1) == 0:
            continue
        source = getattr(display, "Input", None)
        if not source:
            continue
        try:
            source.UpdatePipeline(time_value)
            bounds_items.append(source.GetDataInformation().GetBounds())
        except Exception as exc:
            print(f"Warning: could not read visible representation bounds: {exc}")

    if not bounds_items and fallback_source is not None:
        try:
            fallback_source.UpdatePipeline(time_value)
            bounds_items.append(fallback_source.GetDataInformation().GetBounds())
        except Exception as exc:
            print(f"Warning: could not read fallback source bounds: {exc}")

    return merge_bounds(bounds_items)


def configure_state_camera_to_bounds(view, bounds):
    position = list(getattr(view, "CameraPosition", [0.0, 0.0, 1.0]))
    focal = list(getattr(view, "CameraFocalPoint", [0.0, 0.0, 0.0]))
    normal = vector_normalize(vector_add(position, vector_scale(focal, -1.0)))
    up = vector_normalize(list(getattr(view, "CameraViewUp", [0.0, 1.0, 0.0])))
    right = vector_normalize(vector_cross(up, normal))
    up = vector_normalize(vector_cross(normal, right))

    width, height, center = projected_bounds(bounds, right, up)
    target_aspect = width / max(height, 1e-12)
    desired_size = image_size_from_bounds(width, height)
    image_size = fit_view_size_to_aspect(view, desired_size, target_aspect)
    image_aspect = float(image_size[0]) / float(image_size[1])
    distance = vector_length(vector_add(position, vector_scale(focal, -1.0)))

    view.CameraFocalPoint = center
    view.CameraPosition = vector_add(center, vector_scale(normal, distance))
    view.CameraViewUp = up
    set_if_property(view, "CameraParallelProjection", 1)
    half_height = max(height * 0.5, (width * 0.5) / image_aspect, 1e-9)
    set_if_property(view, "CameraParallelScale", half_height * MESH_PADDING)
    return image_size


def configure_state_visible_bounds_camera(view, time_value, fallback_source=None):
    bounds = state_visible_bounds(view, time_value, fallback_source)
    if not bounds:
        print("Warning: could not determine visible state bounds; falling back to state view aspect.")
        return view_image_size_from_current_aspect(view)
    return configure_state_camera_to_bounds(view, bounds)


def save_state_screenshot(output_file, view, image_size):
    # Keep exported PNGs free of the ParaView viewport background. The white
    # background is only a fallback for ParaView builds that ignore transparency.
    set_if_property(view, "UseColorPaletteForBackground", 0)
    set_if_property(view, "UseGradientBackground", 0)
    set_if_property(view, "UseTexturedBackground", 0)
    set_if_property(view, "Background", [1.0, 1.0, 1.0])
    set_if_property(view, "Background2", [1.0, 1.0, 1.0])
    try:
        SaveScreenshot(
            str(output_file),
            view,
            ImageResolution=image_size,
            TransparentBackground=1,
        )
    except TypeError:
        SaveScreenshot(str(output_file), view, ImageResolution=image_size)


def pvsm_reader_reference(state_file):
    try:
        root = ET.parse(str(state_file)).getroot()
    except Exception as exc:
        print(f"Warning: could not parse pvsm reader reference: {exc}")
        return None

    fallback = None
    for proxy in root.iter():
        if proxy.tag != "Proxy":
            continue
        proxy_type = proxy.attrib.get("type", "")
        proxy_label = " ".join(
            str(proxy.attrib.get(key, "")) for key in ("type", "group", "label", "name")
        )
        if "OpenFOAMReader" in proxy_type or "OpenFOAM" in proxy_label:
            ref = {}
            if proxy.attrib.get("id"):
                ref["id"] = proxy.attrib["id"]
            if proxy.attrib.get("name"):
                ref["name"] = proxy.attrib["name"]
            if ref:
                return ref
        if fallback is None:
            for prop in proxy:
                if prop.attrib.get("name") == "FileName":
                    ref = {}
                    if proxy.attrib.get("id"):
                        ref["id"] = proxy.attrib["id"]
                    if proxy.attrib.get("name"):
                        ref["name"] = proxy.attrib["name"]
                    fallback = ref or None
                    break
    return fallback


def pvsm_color_field(state_file):
    try:
        root = ET.parse(str(state_file)).getroot()
    except Exception:
        return ""
    for prop in root.iter():
        if prop.tag != "Property" or prop.attrib.get("name") != "ColorArrayName":
            continue
        values = [
            element.attrib.get("value", "")
            for element in prop
            if element.tag == "Element" and element.attrib.get("value", "")
        ]
        for value in reversed(values):
            if value and value not in ("0", "1"):
                return value
    return ""


def pvsm_property(proxy, property_name):
    for prop in proxy:
        if prop.tag == "Property" and prop.attrib.get("name") == property_name:
            return prop
    return None


def pvsm_property_values(proxy, property_name):
    prop = pvsm_property(proxy, property_name)
    if prop is None:
        return []
    return [
        element.attrib.get("value", "")
        for element in prop
        if element.tag == "Element"
    ]


def pvsm_property_proxy_ids(proxy, property_name):
    prop = pvsm_property(proxy, property_name)
    if prop is None:
        return []
    return [
        element.attrib.get("value", "")
        for element in prop
        if element.tag == "Proxy" and element.attrib.get("value", "")
    ]


def pvsm_first_property_value(proxy, property_name, default=""):
    values = pvsm_property_values(proxy, property_name)
    return values[0] if values else default


def pvsm_field_tokens(values):
    tokens = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if text.upper() in ("POINTS", "POINT_DATA", "CELLS", "CELL_DATA"):
            continue
        try:
            float(text)
            continue
        except ValueError:
            tokens.append(text)
    return tokens


def pvsm_proxy_label(proxy):
    return (
        proxy.attrib.get("name")
        or proxy.attrib.get("label")
        or f"{proxy.attrib.get('type', 'Proxy')}#{proxy.attrib.get('id', '?')}"
    )


def pvsm_proxy_type(proxies, proxy_id):
    proxy = proxies.get(proxy_id)
    if proxy is None:
        return ""
    return proxy.attrib.get("type", "")


def pvsm_clip_function_types(proxy, proxies):
    function_ids = []
    for property_name in ("ClipFunction", "HyperTreeGridClipFunction"):
        function_ids.extend(pvsm_property_proxy_ids(proxy, property_name))
    return [pvsm_proxy_type(proxies, proxy_id) for proxy_id in function_ids]


def pvsm_clip_is_scalar(proxy, proxies):
    return any(function_type == "Scalar" for function_type in pvsm_clip_function_types(proxy, proxies))


def pvsm_scalar_filter_info(proxy, proxies):
    proxy_type = proxy.attrib.get("type", "")
    if proxy_type == "Clip" and not pvsm_clip_is_scalar(proxy, proxies):
        return None

    scalar_property_names = {
        "Clip": ("SelectInputScalars",),
        "Threshold": ("Scalars", "InputScalars"),
        "Contour": ("ContourBy",),
        "ExtractSelection": ("Selection",),
    }
    property_names = scalar_property_names.get(proxy_type, ())
    fields = []
    for property_name in property_names:
        fields.extend(pvsm_field_tokens(pvsm_property_values(proxy, property_name)))
    if not fields:
        return None

    details = {
        "id": proxy.attrib.get("id", ""),
        "name": pvsm_proxy_label(proxy),
        "type": proxy_type,
        "fields": sorted(set(fields)),
    }
    if proxy_type == "Clip":
        details["clip_function"] = "/".join(
            function_type
            for function_type in pvsm_clip_function_types(proxy, proxies)
            if function_type
        )
    value = pvsm_first_property_value(proxy, "Value")
    if value:
        details["value"] = value
    lower = pvsm_first_property_value(proxy, "LowerThreshold")
    upper = pvsm_first_property_value(proxy, "UpperThreshold")
    if lower or upper:
        details["threshold"] = f"{lower} .. {upper}"
    isosurfaces = pvsm_property_values(proxy, "Isosurfaces")
    if isosurfaces:
        details["isosurfaces"] = ", ".join(isosurfaces[:5])
    return details


def pvsm_visible_representation_input_ids(proxies):
    input_ids = []
    for proxy in proxies.values():
        if proxy.attrib.get("group") != "representations":
            continue
        if pvsm_first_property_value(proxy, "Visibility", "0") == "0":
            continue
        input_ids.extend(pvsm_property_proxy_ids(proxy, "Input"))
    return input_ids


def pvsm_upstream_proxy_ids(proxy):
    result = []
    for property_name in ("Input", "Source"):
        for proxy_id in pvsm_property_proxy_ids(proxy, property_name):
            if proxy_id in result:
                continue
            result.append(proxy_id)
    return result


def pvsm_visible_scalar_geometry_risks(state_file):
    try:
        root = ET.parse(str(state_file)).getroot()
    except Exception as exc:
        print(f"Warning: could not inspect pvsm geometry filters: {exc}")
        return []

    proxies = {
        proxy.attrib.get("id", ""): proxy
        for proxy in root.iter()
        if proxy.tag == "Proxy" and proxy.attrib.get("id")
    }
    risks = []
    for input_id in pvsm_visible_representation_input_ids(proxies):
        stack = [input_id]
        seen = set()
        while stack:
            proxy_id = stack.pop()
            if proxy_id in seen:
                continue
            seen.add(proxy_id)
            proxy = proxies.get(proxy_id)
            if proxy is None:
                continue
            info = pvsm_scalar_filter_info(proxy, proxies)
            if info and info not in risks:
                risks.append(info)
            stack.extend(pvsm_upstream_proxy_ids(proxy))
    return risks


def handle_state_scalar_geometry_risks(state_file):
    if STATE_SCALAR_FILTER_POLICY == "allow":
        return
    risks = pvsm_visible_scalar_geometry_risks(state_file)
    if not risks:
        return

    print("WARNING: visible scalar geometry filter(s) found in the ParaView state:")
    for risk in risks:
        detail_parts = [f"{risk['name']} ({risk['type']})"]
        if risk.get("clip_function"):
            detail_parts.append("clip_function=" + risk["clip_function"])
        if risk.get("fields"):
            detail_parts.append("field=" + "/".join(risk["fields"]))
        if risk.get("value"):
            detail_parts.append("value=" + risk["value"])
        if risk.get("threshold"):
            detail_parts.append("threshold=" + risk["threshold"])
        if risk.get("isosurfaces"):
            detail_parts.append("isosurfaces=" + risk["isosurfaces"])
        print("  - " + ", ".join(detail_parts))

    action = "stop" if STATE_SCALAR_FILTER_POLICY == "auto" and FIELD_POLICY == "override" else "warn"
    if action == "stop":
        raise RuntimeError(
            "The .pvsm state contains visible scalar geometry filters while Field policy is "
            "Override field. This can create nonphysical sharp boundaries in fields such as p. "
            "Save a clean state without scalar Clip/Threshold/Contour, use Generated pipeline, "
            "or set Scalar geometry filters to Warn only/Allow if this geometry is intentional."
        )
    print(
        "Warning only: scalar geometry filters can create sharp artificial boundaries. "
        "Use a clean state if you want to compare full-field scalar values."
    )


def reset_pipeline():
    reset_session = globals().get("ResetSession")
    if reset_session:
        reset_session()
        return
    for proxy in list(GetSources().values()):
        try:
            Delete(proxy)
        except Exception:
            pass


def source_xml_name(source):
    try:
        return source.SMProxy.GetXMLName()
    except Exception:
        return source.__class__.__name__


def find_openfoam_reader():
    fallback = None
    for _key, source in GetSources().items():
        props = source.ListProperties()
        if "FileName" not in props:
            continue
        name = source_xml_name(source)
        file_name = str(getattr(source, "FileName", ""))
        if "OpenFOAM" in name or file_name.lower().endswith(".foam"):
            return source
        if fallback is None:
            fallback = source
    return fallback


def replace_reader_file(reader, case_file):
    if not reader or "FileName" not in reader.ListProperties():
        return False
    reader.FileName = str(case_file)
    reader.UpdatePipelineInformation()
    return True


def configure_state_reader_arrays(reader):
    if FIELD_POLICY != "override":
        return

    if FIELD_ASSOCIATION.upper() == "CELLS":
        set_if_property(reader, "CellArrays", [FIELD_NAME])
    elif FIELD_ASSOCIATION.upper() == "POINTS":
        set_if_property(reader, "PointArrays", [FIELD_NAME])
    reader.UpdatePipelineInformation()


def load_state_for_case(state_file, case_file, reader_ref):
    loaded_with_filename = False
    if reader_ref:
        mapping = dict(reader_ref)
        mapping["FileName"] = str(case_file)
        try:
            LoadState(str(state_file), filenames=[mapping])
            loaded_with_filename = True
        except Exception as exc:
            print(f"Warning: LoadState filename replacement failed: {exc}")

    if not loaded_with_filename:
        LoadState(str(state_file))

    reader = find_openfoam_reader()
    if not reader:
        raise RuntimeError("Could not find an OpenFOAM reader or FileName source in the loaded state.")

    if not loaded_with_filename and not replace_reader_file(reader, case_file):
        raise RuntimeError("Could not replace the state reader FileName.")

    configure_state_reader_arrays(reader)
    reader.UpdatePipelineInformation()
    return reader


def render_view_from_state():
    get_render_views = globals().get("GetRenderViews")
    if get_render_views:
        views = list(get_render_views())
        if views:
            return views[0]
    return GetActiveViewOrCreate("RenderView")


def visible_representations(view):
    get_representations = globals().get("GetRepresentations")
    if not get_representations:
        return []
    def as_list(value):
        if isinstance(value, dict):
            return list(value.values())
        return list(value)
    try:
        return as_list(get_representations(view))
    except Exception:
        try:
            return as_list(get_representations())
        except Exception:
            return []


ASSOCIATION_ALIASES = {
    "POINTS": "POINTS",
    "POINT_DATA": "POINTS",
    "POINTDATA": "POINTS",
    "CELLS": "CELLS",
    "CELL_DATA": "CELLS",
    "CELLDATA": "CELLS",
}


def normalize_association_token(value):
    return ASSOCIATION_ALIASES.get(str(value).strip().upper(), "")


def color_array_value_parts(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip().strip("'\"") for part in re.split(r"[,;\[\]\(\)]", text) if part.strip()]


def is_non_field_color_token(value):
    text = str(value).strip()
    if not text:
        return True
    if normalize_association_token(text):
        return True
    try:
        float(text)
        return True
    except ValueError:
        return False


def color_array_info_from_value(value):
    parts = color_array_value_parts(value)
    association = ""
    for part in parts:
        normalized = normalize_association_token(part)
        if normalized:
            association = normalized

    field_name = ""
    for part in reversed(parts):
        if not is_non_field_color_token(part):
            field_name = part
            break
    return field_name, association


def display_color_info(display, fallback_field=""):
    try:
        field_name, association = color_array_info_from_value(
            getattr(display, "ColorArrayName", None)
        )
    except Exception:
        field_name, association = "", ""
    return field_name or fallback_field, association


def display_color_component(display, field_name):
    lut = getattr(display, "LookupTable", None)
    if lut is None and field_name:
        try:
            lut = GetColorTransferFunction(field_name)
        except Exception:
            lut = None
    if lut is None:
        return "Magnitude"

    try:
        mode = str(getattr(lut, "VectorMode", "")).strip().lower()
        if mode in ("component", "1"):
            return int(getattr(lut, "VectorComponent", 0))
    except Exception:
        pass
    return "Magnitude"


def state_field_label(view, state_color_field=""):
    if FIELD_POLICY == "override":
        return FIELD_NAME
    if state_color_field:
        return state_color_field
    for display in visible_representations(view):
        field_name, _association = display_color_info(display)
        if field_name:
            return field_name
    return "state"


def override_state_field(view, shared_color_range=None, rescale=True):
    if FIELD_POLICY != "override":
        return
    for display in visible_representations(view):
        try:
            if FIELD_COMPONENT:
                ColorBy(display, (FIELD_ASSOCIATION.upper(), FIELD_NAME, FIELD_COMPONENT))
            else:
                ColorBy(display, (FIELD_ASSOCIATION.upper(), FIELD_NAME))
            set_if_property(display, "Opacity", 1.0)
            set_if_property(display, "UseSeparateOpacityArray", 0)
            set_if_property(display, "EnableOpacityMapping", 0)
            try:
                GetColorTransferFunction(FIELD_NAME).ApplyPreset("Cool to Warm", True)
            except Exception as preset_exc:
                print(f"Warning: could not reset color preset for {FIELD_NAME}: {preset_exc}")
            if rescale:
                rescale_display_color(display, shared_color_range)
        except Exception as exc:
            print(f"Warning: could not override field on a representation: {exc}")


def state_color_range_target(view, state_color_field=""):
    if FIELD_POLICY == "override":
        return FIELD_NAME, FIELD_ASSOCIATION, FIELD_COMPONENT

    fallback = state_color_field or ""
    for display in visible_representations(view):
        if getattr(display, "Visibility", 1) == 0:
            continue
        field_name, association = display_color_info(display, fallback)
        if not field_name:
            continue
        if state_color_field and field_name != state_color_field:
            continue
        return field_name, association, display_color_component(display, field_name)
    return fallback, "", "Magnitude"


def display_has_lookup_table(display):
    try:
        return bool(getattr(display, "LookupTable", None))
    except Exception:
        return False


def apply_range_to_state_displays(view, target_field, range_values, state_color_field=""):
    applied_range = None
    touched = False
    for display in visible_representations(view):
        if getattr(display, "Visibility", 1) == 0:
            continue
        field_name, _association = display_color_info(display)
        if not field_name and state_color_field and display_has_lookup_table(display):
            field_name = state_color_field
        if not field_name:
            continue
        if target_field and field_name != target_field:
            continue
        try:
            applied_range = rescale_transfer_functions(field_name, range_values, display)
            touched = True
        except Exception as exc:
            print(f"Warning: could not apply color range to {field_name}: {exc}")

    if not touched and target_field:
        try:
            applied_range = rescale_transfer_functions(target_field, range_values)
            touched = applied_range is not None
        except Exception as exc:
            print(f"Warning: could not apply color range to {target_field}: {exc}")

    return applied_range, touched


def apply_state_color_range(view, state_color_field="", shared_color_range=None, time_value=None):
    if COLOR_RANGE_MODE == "state_file":
        return None

    if FIELD_POLICY == "override":
        override_state_field(
            view,
            shared_color_range,
            rescale=COLOR_RANGE_MODE in ("custom", "all_times"),
        )
        if COLOR_RANGE_MODE in ("data", "visible"):
            if COLOR_RANGE_MODE == "data":
                value_range = state_data_field_range(
                    view,
                    time_value,
                    FIELD_NAME,
                    FIELD_ASSOCIATION,
                    FIELD_COMPONENT,
                )
            else:
                value_range = state_visible_field_range(
                    view,
                    time_value,
                    FIELD_NAME,
                    FIELD_ASSOCIATION,
                    FIELD_COMPONENT,
                    allow_pipeline_fallback=False,
                )
            if value_range:
                applied_range, _touched = apply_range_to_state_displays(
                    view,
                    FIELD_NAME,
                    value_range,
                    state_color_field,
                )
                return applied_range
            else:
                print(f"Warning: could not compute single-frame range for {FIELD_NAME}")
        return None

    target_field, target_association, target_component = state_color_range_target(
        view, state_color_field
    )
    if COLOR_RANGE_MODE in ("data", "visible"):
        if not target_field:
            print(
                "Warning: single-frame color range requested in state coloring mode, "
                "but no visible colored representation was found."
            )
            return
        if COLOR_RANGE_MODE == "data":
            value_range = state_data_field_range(
                view,
                time_value,
                target_field,
                target_association,
                target_component,
            )
        else:
            value_range = state_visible_field_range(
                view,
                time_value,
                target_field,
                target_association,
                target_component,
                allow_pipeline_fallback=False,
            )
        if value_range:
            applied_range, _touched = apply_range_to_state_displays(
                view,
                target_field,
                value_range,
                state_color_field,
            )
            return applied_range
        else:
            print(f"Warning: could not compute single-frame range for {target_field}")
        return None

    range_values = COLOR_RANGE if COLOR_RANGE_MODE == "custom" else shared_color_range
    target_field = FIELD_NAME if FIELD_POLICY == "override" else (state_color_field or target_field)
    applied_range, touched = apply_range_to_state_displays(
        view,
        target_field,
        range_values,
        state_color_field,
    )

    if not touched and state_color_field and COLOR_RANGE_MODE in ("custom", "all_times"):
        try:
            applied_range = rescale_display_color_for_field(
                None,
                state_color_field,
                shared_color_range,
            )
            touched = True
        except Exception as exc:
            print(f"Warning: could not apply state color range to {state_color_field}: {exc}")

    if not touched and COLOR_RANGE_MODE in ("custom", "all_times"):
        print(
            "Warning: color range mode requested in state coloring mode, "
            "but no visible colored representation was found."
        )
    return applied_range


def state_visible_field_range(
    view,
    time_value,
    field_name="",
    association="",
    component=None,
    allow_pipeline_fallback=True,
):
    ranges = []
    for display in visible_representations(view):
        if getattr(display, "Visibility", 1) == 0:
            continue
        source = getattr(display, "Input", None)
        if not source:
            continue
        display_field, display_association = display_color_info(display, field_name)
        active_field = field_name or display_field
        if not active_field:
            continue
        if field_name and display_field and display_field != field_name:
            continue
        active_association = association or display_association or None
        active_component = (
            component
            if component is not None
            else display_color_component(display, active_field)
        )
        try:
            source.UpdatePipeline(time_value)
            fetched = servermanager.Fetch(source)
            ranges.extend(
                collect_ranges(
                    fetched,
                    active_field,
                    active_association,
                    active_component,
                )
            )
        except Exception as exc:
            print(f"Warning: could not compute state field range: {exc}")
    if not ranges and field_name and allow_pipeline_fallback:
        ranges = pipeline_field_ranges(time_value, field_name, association, component)
    if not ranges:
        return None
    return [min(item[0] for item in ranges), max(item[1] for item in ranges)]


def state_data_field_range(view, time_value, field_name="", association="", component=None):
    value_range = state_visible_field_range(
        view,
        time_value,
        field_name,
        association,
        component,
        allow_pipeline_fallback=True,
    )
    if value_range:
        return value_range
    ranges = pipeline_field_ranges(time_value, field_name, association, component)
    if not ranges:
        return None
    return [min(item[0] for item in ranges), max(item[1] for item in ranges)]


def pipeline_field_ranges(time_value, field_name, association="", component=None):
    ranges = []
    for _key, source in GetSources().items():
        try:
            source.UpdatePipeline(time_value)
            fetched = servermanager.Fetch(source)
        except Exception:
            continue

        source_ranges = collect_ranges(fetched, field_name, association or None, component)
        if not source_ranges and association:
            source_ranges = collect_ranges(fetched, field_name, None, component)
        ranges.extend(source_ranges)
    return ranges


def compute_state_shared_color_range(case_items, reader_ref, state_color_field):
    if COLOR_RANGE_MODE != "all_times":
        return None

    combined = []
    for item in case_items:
        label = item.get("case_name") or item.get("case_file") or item.get("case_dir")
        try:
            reset_pipeline()
            case_file = as_path(item["case_file"])
            case_dir = as_path(item.get("case_dir") or case_file.parent)
            reader = load_state_for_case(PVSM_STATE_FILE, case_file, reader_ref)
            view = render_view_from_state()
            override_state_field(view, None, rescale=False)
            target_field, target_association, target_component = state_color_range_target(
                view, state_color_field
            )
            if not target_field:
                raise RuntimeError("state has no visible colored field to scan")
            for time_value in available_times(reader, case_dir):
                reader.UpdatePipeline(time_value)
                value_range = state_data_field_range(
                    view,
                    time_value,
                    target_field,
                    target_association,
                    target_component,
                )
                if value_range:
                    combined.append(value_range)
        except Exception as exc:
            print(f"Warning: skipped color range prepass for {label}: {exc}")

    reset_pipeline()
    if not combined:
        field_label = FIELD_NAME if FIELD_POLICY == "override" else (state_color_field or "state")
        raise RuntimeError(f"Could not compute unified state color range for field {field_label}")
    return [min(item[0] for item in combined), max(item[1] for item in combined)]


def update_existing_box_clip():
    updated = False
    for _key, source in GetSources().items():
        if "ClipType" not in source.ListProperties():
            continue
        try:
            source.ClipType = "Box"
            set_if_property(source.ClipType, "Position", BOX_POSITION)
            set_if_property(source.ClipType, "Length", BOX_LENGTH)
            set_if_property(source, "Invert", 0)
            source.UpdatePipeline()
            updated = True
        except Exception as exc:
            print(f"Warning: could not update an existing Box Clip: {exc}")
    if ROI_MODE == "clip_box" and not updated:
        print("Warning: ROI mode Clip to box requested, but no existing Clip filter was found in the state; using camera zoom only.")
    return updated


def state_case_items():
    if BATCH_CASES:
        return BATCH_CASES
    return [
        {
            "case_name": CASE_FILE.stem if str(CASE_FILE) else CASE_DIR.name,
            "case_dir": str(CASE_DIR),
            "case_file": str(CASE_FILE),
            "output_dir": str(OUTPUT_DIR),
        }
    ]


def run_state_case(case_item, reader_ref, state_color_field, shared_color_range=None):
    case_file = as_path(case_item["case_file"])
    case_dir = as_path(case_item.get("case_dir") or case_file.parent)
    case_name = safe_path_part(case_item.get("case_name") or case_dir.name)
    case_output = as_path(case_item.get("output_dir") or (OUTPUT_ROOT / case_name))

    if not case_file.exists():
        raise FileNotFoundError(f"case_file does not exist: {case_file}")

    reset_pipeline()
    reader = load_state_for_case(PVSM_STATE_FILE, case_file, reader_ref)
    disable_reset = globals().get("DisableFirstRenderCameraReset")
    if disable_reset:
        disable_reset()
    view = render_view_from_state()
    field_label = state_field_label(view, state_color_field)
    output_dir = case_output / safe_path_part(field_label)
    output_dir.mkdir(parents=True, exist_ok=True)

    times = selected_times(reader, case_dir)
    for time_index, time_value in enumerate(times, start=1):
        view.ViewTime = time_value
        reader.UpdatePipeline(time_value)
        applied_color_range = apply_state_color_range(
            view,
            state_color_field,
            shared_color_range,
            time_value,
        )
        if COLOR_RANGE_MODE in ("data", "visible") and applied_color_range:
            print(
                "Single-frame color range "
                f"for {field_label} at t={time_value:g}: "
                f"{format_color_range(applied_color_range)}"
            )
        image_size = configure_state_visible_bounds_camera(view, time_value, reader)
        Render(view)
        apply_state_color_range(view, state_color_field, shared_color_range, time_value)
        image_size = configure_state_visible_bounds_camera(view, time_value, reader)
        output_file = output_dir / state_screenshot_name(
            case_name, time_index, time_value, field_label
        )
        save_state_screenshot(output_file, view, image_size)
        print(f"Saved {output_file}")


def main_pvsm():
    if not PVSM_STATE_FILE.exists():
        raise FileNotFoundError(f"pvsm_state_file does not exist: {PVSM_STATE_FILE}")

    disable_reset = globals().get("DisableFirstRenderCameraReset")
    if disable_reset:
        disable_reset()

    reader_ref = pvsm_reader_reference(PVSM_STATE_FILE)
    state_color_field = pvsm_color_field(PVSM_STATE_FILE)
    print(
        "ParaView state mode: using state coloring field "
        f"{state_color_field or '(detected from visible representation)'}."
    )
    print(f"Color range mode: {COLOR_RANGE_MODE}")
    handle_state_scalar_geometry_risks(PVSM_STATE_FILE)
    case_items = state_case_items()
    shared_color_range = compute_state_shared_color_range(
        case_items, reader_ref, state_color_field
    )
    if shared_color_range:
        print(
            "Unified state color range over selected cases/times: "
            f"[{shared_color_range[0]:g}, {shared_color_range[1]:g}]"
        )
    failures = []
    for item in case_items:
        label = item.get("case_name") or item.get("case_dir") or item.get("case_file")
        try:
            print(f"Processing state case: {label}")
            run_state_case(item, reader_ref, state_color_field, shared_color_range)
        except Exception as exc:
            failures.append((label, exc))
            print(f"ERROR processing {label}: {exc}")
            traceback.print_exc()

    if failures:
        print("State-mode completed with failures:")
        for label, exc in failures:
            print(f"  {label}: {exc}")
        raise RuntimeError(f"{len(failures)} case(s) failed in state mode")


def main_generated():
    case_file = as_path(CASE_FILE)
    output_dir = as_path(OUTPUT_DIR) / safe_path_part(FIELD_NAME)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not case_file.exists():
        raise FileNotFoundError(f"CASE_FILE does not exist: {case_file}")

    print(
        "Generated pipeline mode: coloring by GUI field "
        f"{FIELD_NAME}"
        + (f" ({FIELD_COMPONENT})" if FIELD_COMPONENT else "")
    )
    print(f"Color range mode: {COLOR_RANGE_MODE}")

    disable_reset = globals().get("DisableFirstRenderCameraReset")
    if disable_reset:
        disable_reset()

    reader = configure_openfoam_reader(case_file)
    source = maybe_clip(reader)
    times = selected_times(reader)
    shared_color_range = (
        compute_shared_color_range(source, times)
        if COLOR_RANGE_MODE == "all_times"
        else None
    )
    if shared_color_range:
        print(
            "Auto color range over selected times: "
            f"[{shared_color_range[0]:g}, {shared_color_range[1]:g}]"
        )

    view = CreateView("RenderView")
    view.Background = BACKGROUND
    view.OrientationAxesVisibility = 1 if SHOW_ORIENTATION_AXES else 0

    for time_index, time_value in enumerate(times, start=1):
        view.ViewTime = time_value
        reader.UpdatePipeline(time_value)

        for angle_index, angle_deg in enumerate(ANGLES_DEG, start=1):
            slice_filter, normal = build_slice(source, angle_deg)
            slice_filter.UpdatePipeline(time_value)

            single_frame_range = (
                range_for_slice(slice_filter)
                if COLOR_RANGE_MODE in ("data", "visible")
                else None
            )
            display = configure_display(
                slice_filter,
                view,
                shared_color_range,
                single_frame_range,
            )
            if single_frame_range:
                print(
                    "Single-frame color range "
                    f"for {FIELD_NAME} at t={time_value:g}, angle={angle_deg:g}: "
                    f"{format_color_range(single_frame_range)}"
                )
            vector_proxies = []
            if VISUALIZATION_TYPE == "vector_arrows":
                vector_proxies = build_vector_arrows(slice_filter, view)

            image_size = configure_camera(view, slice_filter, normal)
            Render(view)

            output_file = output_dir / screenshot_name(
                case_file, time_index, time_value, angle_index, angle_deg
            )
            SaveScreenshot(str(output_file), view, ImageResolution=image_size)
            print(f"Saved {output_file}")

            if vector_proxies:
                point_data, glyph, glyph_display = vector_proxies
                Hide(glyph, view)
                Delete(glyph_display)
                Delete(glyph)
                Delete(point_data)

            Hide(slice_filter, view)
            Delete(display)
            Delete(slice_filter)

    Delete(view)


def main():
    main_pvsm()


if __name__ == "__main__":
    main()
