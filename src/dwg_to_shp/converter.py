from __future__ import annotations

import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


class CadConversionError(RuntimeError):
    """Raised when a CAD file cannot be converted."""


@dataclass(slots=True)
class ConversionOptions:
    closed_polylines_as_polygons: bool = True
    curve_segments: int = 72
    curve_tolerance: float = 0.25
    prefer_ogr2ogr: bool = True
    prj_path: Path | None = None


@dataclass(slots=True)
class ConversionResult:
    input_path: Path
    output_dir: Path
    engine: str
    shapefiles: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_entities: int = 0


@dataclass(slots=True)
class _Feature:
    geometry: Any
    attrs: dict[str, Any]


_SUPPORTED_EXTENSIONS = {".dwg", ".dxf"}


def convert_cad_to_shapefiles(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    options: ConversionOptions | None = None,
) -> ConversionResult:
    """Convert a DWG or DXF file into one or more ESRI shapefiles."""
    options = options or ConversionOptions()
    input_path = Path(input_path).expanduser().resolve()
    if not input_path.exists():
        raise CadConversionError(f"Input file does not exist: {input_path}")
    if input_path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        raise CadConversionError("Input must be a .dwg or .dxf file.")

    output_dir = Path(output_dir or input_path.with_suffix("")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if options.prefer_ogr2ogr and shutil.which("ogr2ogr"):
        try:
            result = _convert_with_ogr2ogr(input_path, output_dir, options)
            if result.shapefiles:
                return result
            warnings.append("ogr2ogr completed but did not create any .shp files.")
        except CadConversionError as exc:
            warnings.append(f"ogr2ogr failed, falling back to ezdxf: {exc}")

    if input_path.suffix.lower() == ".dwg":
        doc = _read_dwg_with_odafc(input_path)
        engine = "ezdxf + ODA File Converter"
    else:
        doc = _read_dxf(input_path)
        engine = "ezdxf"

    features, skipped = _collect_features(doc, input_path.stem, options)
    shapefiles = _write_feature_sets(output_dir, input_path.stem, features, options)
    warnings.extend(_empty_geometry_warnings(features))

    if not shapefiles:
        raise CadConversionError("No supported CAD entities were found to write.")

    return ConversionResult(
        input_path=input_path,
        output_dir=output_dir,
        engine=engine,
        shapefiles=shapefiles,
        warnings=warnings,
        skipped_entities=skipped,
    )


def _convert_with_ogr2ogr(
    input_path: Path,
    output_dir: Path,
    options: ConversionOptions,
) -> ConversionResult:
    before = {p.resolve() for p in output_dir.glob("*.shp")}
    command = [
        "ogr2ogr",
        "-overwrite",
        "-skipfailures",
        "-f",
        "ESRI Shapefile",
        str(output_dir),
        str(input_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise CadConversionError(detail or "ogr2ogr returned a non-zero exit code.")

    shapefiles = sorted(p for p in output_dir.glob("*.shp") if p.resolve() not in before)
    if not shapefiles:
        shapefiles = sorted(output_dir.glob("*.shp"))
    for shp_path in shapefiles:
        _copy_projection(options.prj_path, shp_path)

    return ConversionResult(
        input_path=input_path,
        output_dir=output_dir,
        engine="ogr2ogr",
        shapefiles=shapefiles,
    )


def _read_dxf(input_path: Path):
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as exc:
        raise CadConversionError(
            "DXF conversion requires ezdxf. Install dependencies with: py -m pip install -r requirements.txt"
        ) from exc

    try:
        doc, auditor = recover.readfile(str(input_path))
        if auditor.has_errors:
            fixed_doc = doc
            return fixed_doc
        return doc
    except Exception:
        try:
            return ezdxf.readfile(str(input_path))
        except Exception as exc:
            raise CadConversionError(f"Could not read DXF file: {exc}") from exc


def _read_dwg_with_odafc(input_path: Path):
    try:
        from ezdxf.addons import odafc
    except ImportError as exc:
        raise CadConversionError(
            "DWG input requires ezdxf and ODA File Converter, or GDAL ogr2ogr in PATH. "
            "DXF files do not need ODA."
        ) from exc

    try:
        return odafc.readfile(str(input_path))
    except Exception as exc:
        raise CadConversionError(
            "Could not read DWG. Install ODA File Converter and make it discoverable in PATH, "
            "or install GDAL/ogr2ogr with DWG support. You can also save the drawing as DXF and retry. "
            f"Original error: {exc}"
        ) from exc


def _collect_features(doc: Any, base_name: str, options: ConversionOptions) -> tuple[dict[str, list[_Feature]], int]:
    features: dict[str, list[_Feature]] = {
        "points": [],
        "lines": [],
        "polygons": [],
    }
    skipped = 0

    try:
        modelspace = doc.modelspace()
    except Exception as exc:
        raise CadConversionError(f"Could not open CAD modelspace: {exc}") from exc

    for entity in modelspace:
        try:
            produced = _entity_to_features(entity, options)
        except Exception:
            skipped += 1
            continue
        if not produced:
            skipped += 1
            continue
        for geometry_type, feature in produced:
            features[geometry_type].append(feature)

    return features, skipped


def _entity_to_features(entity: Any, options: ConversionOptions) -> list[tuple[str, _Feature]]:
    entity_type = entity.dxftype()
    attrs = _base_attrs(entity)

    if entity_type == "POINT":
        point = _xy(_dxf_get(entity, "location"))
        attrs["elev"] = _z(_dxf_get(entity, "location"))
        return [("points", _Feature(point, attrs))]

    if entity_type == "INSERT":
        point = _xy(_dxf_get(entity, "insert"))
        attrs["block"] = str(_dxf_get(entity, "name", ""))
        attrs["elev"] = _z(_dxf_get(entity, "insert"))
        return [("points", _Feature(point, attrs))]

    if entity_type in {"TEXT", "MTEXT"}:
        point = _text_insert_point(entity)
        attrs["text"] = _entity_text(entity)
        attrs["elev"] = _z(point)
        return [("points", _Feature(_xy(point), attrs))]

    if entity_type == "LINE":
        start = _dxf_get(entity, "start")
        end = _dxf_get(entity, "end")
        attrs["elev"] = _z(start)
        return [("lines", _Feature([_xy(start), _xy(end)], attrs))]

    if entity_type in {"LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}:
        points = _curve_points(entity, options)
        if len(points) < 2:
            return []

        is_closed = _entity_is_closed(entity, points)
        attrs["closed"] = "Y" if is_closed else "N"
        if is_closed and options.closed_polylines_as_polygons and len(points) >= 3:
            ring = _closed_ring(points)
            if len(ring) >= 4:
                return [("polygons", _Feature(ring, attrs))]
        return [("lines", _Feature(points, attrs))]

    return []


def _curve_points(entity: Any, options: ConversionOptions) -> list[tuple[float, float]]:
    try:
        from ezdxf import path as ezpath

        cad_path = ezpath.make_path(entity)
        points = [_xy(point) for point in cad_path.flattening(distance=options.curve_tolerance, segments=8)]
        if points:
            return _dedupe_consecutive(points)
    except Exception:
        pass

    entity_type = entity.dxftype()
    if entity_type == "LWPOLYLINE":
        return _dedupe_consecutive([_xy(point) for point in entity.vertices_in_wcs()])
    if entity_type == "POLYLINE":
        return _dedupe_consecutive([_xy(vertex.dxf.location) for vertex in entity.vertices])
    if entity_type == "ARC":
        return _arc_points(entity, options.curve_segments)
    if entity_type == "CIRCLE":
        return _circle_points(entity, options.curve_segments)
    if entity_type == "ELLIPSE":
        return _ellipse_points(entity, options.curve_segments)
    if entity_type == "SPLINE":
        try:
            return _dedupe_consecutive([_xy(point) for point in entity.flattening(options.curve_tolerance)])
        except Exception:
            return []
    return []


def _arc_points(entity: Any, curve_segments: int) -> list[tuple[float, float]]:
    center = _dxf_get(entity, "center")
    radius = float(_dxf_get(entity, "radius", 0.0))
    start_angle = math.radians(float(_dxf_get(entity, "start_angle", 0.0)))
    end_angle = math.radians(float(_dxf_get(entity, "end_angle", 360.0)))
    if end_angle <= start_angle:
        end_angle += math.tau
    sweep = end_angle - start_angle
    steps = max(2, int(curve_segments * sweep / math.tau))
    return [
        (
            float(center.x) + math.cos(start_angle + sweep * i / steps) * radius,
            float(center.y) + math.sin(start_angle + sweep * i / steps) * radius,
        )
        for i in range(steps + 1)
    ]


def _circle_points(entity: Any, curve_segments: int) -> list[tuple[float, float]]:
    center = _dxf_get(entity, "center")
    radius = float(_dxf_get(entity, "radius", 0.0))
    steps = max(16, curve_segments)
    return [
        (
            float(center.x) + math.cos(math.tau * i / steps) * radius,
            float(center.y) + math.sin(math.tau * i / steps) * radius,
        )
        for i in range(steps)
    ]


def _ellipse_points(entity: Any, curve_segments: int) -> list[tuple[float, float]]:
    center = _dxf_get(entity, "center")
    major_axis = _dxf_get(entity, "major_axis")
    ratio = float(_dxf_get(entity, "ratio", 1.0))
    start = float(_dxf_get(entity, "start_param", 0.0))
    end = float(_dxf_get(entity, "end_param", math.tau))
    if end <= start:
        end += math.tau
    sweep = end - start
    steps = max(8, int(curve_segments * sweep / math.tau))
    major = (float(major_axis.x), float(major_axis.y))
    minor = (-major[1] * ratio, major[0] * ratio)
    return [
        (
            float(center.x) + math.cos(start + sweep * i / steps) * major[0] + math.sin(start + sweep * i / steps) * minor[0],
            float(center.y) + math.cos(start + sweep * i / steps) * major[1] + math.sin(start + sweep * i / steps) * minor[1],
        )
        for i in range(steps + 1)
    ]


def _write_feature_sets(
    output_dir: Path,
    base_name: str,
    features: dict[str, list[_Feature]],
    options: ConversionOptions,
) -> list[Path]:
    shapefiles: list[Path] = []
    if features["points"]:
        shapefiles.append(_write_shapefile(output_dir / f"{base_name}_points.shp", "POINT", features["points"], options.prj_path))
    if features["lines"]:
        shapefiles.append(_write_shapefile(output_dir / f"{base_name}_lines.shp", "POLYLINE", features["lines"], options.prj_path))
    if features["polygons"]:
        shapefiles.append(_write_shapefile(output_dir / f"{base_name}_polygons.shp", "POLYGON", features["polygons"], options.prj_path))
    return shapefiles


def _write_shapefile(path: Path, shape_type: str, features: list[_Feature], prj_path: Path | None) -> Path:
    try:
        import shapefile
    except ImportError as exc:
        raise CadConversionError(
            "Shapefile writing requires pyshp. Install dependencies with: py -m pip install -r requirements.txt"
        ) from exc

    shape_type_value = {
        "POINT": shapefile.POINT,
        "POLYLINE": shapefile.POLYLINE,
        "POLYGON": shapefile.POLYGON,
    }[shape_type]

    writer = shapefile.Writer(str(path), shapeType=shape_type_value)
    writer.autoBalance = 1
    writer.field("layer", "C", size=80)
    writer.field("cad_type", "C", size=32)
    writer.field("handle", "C", size=32)
    writer.field("color", "N", size=8, decimal=0)
    writer.field("linetype", "C", size=48)
    writer.field("text", "C", size=254)
    writer.field("block", "C", size=80)
    writer.field("elev", "F", size=18, decimal=6)
    writer.field("closed", "C", size=1)

    try:
        for feature in features:
            if shape_type == "POINT":
                writer.point(*feature.geometry)
            elif shape_type == "POLYLINE":
                writer.line([feature.geometry])
            elif shape_type == "POLYGON":
                writer.poly([_orient_polygon_ring(feature.geometry)])
            writer.record(
                _safe_text(feature.attrs.get("layer"), 80),
                _safe_text(feature.attrs.get("cad_type"), 32),
                _safe_text(feature.attrs.get("handle"), 32),
                _safe_int(feature.attrs.get("color")),
                _safe_text(feature.attrs.get("linetype"), 48),
                _safe_text(feature.attrs.get("text"), 254),
                _safe_text(feature.attrs.get("block"), 80),
                _safe_float(feature.attrs.get("elev")),
                _safe_text(feature.attrs.get("closed"), 1),
            )
    finally:
        writer.close()

    _copy_projection(prj_path, path)
    return path


def _copy_projection(prj_path: Path | None, shp_path: Path) -> None:
    if not prj_path:
        return
    source = Path(prj_path).expanduser().resolve()
    if not source.exists():
        raise CadConversionError(f"Projection file does not exist: {source}")
    shutil.copyfile(source, shp_path.with_suffix(".prj"))


def _empty_geometry_warnings(features: dict[str, list[_Feature]]) -> list[str]:
    warnings: list[str] = []
    if not features["points"]:
        warnings.append("No point entities were written.")
    if not features["lines"]:
        warnings.append("No line entities were written.")
    if not features["polygons"]:
        warnings.append("No polygon entities were written.")
    return warnings


def _base_attrs(entity: Any) -> dict[str, Any]:
    return {
        "layer": _dxf_get(entity, "layer", ""),
        "cad_type": entity.dxftype(),
        "handle": _dxf_get(entity, "handle", ""),
        "color": _dxf_get(entity, "color", 0),
        "linetype": _dxf_get(entity, "linetype", ""),
        "text": "",
        "block": "",
        "elev": 0.0,
        "closed": "N",
    }


def _dxf_get(entity: Any, name: str, default: Any = None) -> Any:
    try:
        return entity.dxf.get(name, default)
    except Exception:
        try:
            return getattr(entity.dxf, name)
        except Exception:
            return default


def _text_insert_point(entity: Any) -> Any:
    return _dxf_get(entity, "insert", _dxf_get(entity, "location"))


def _entity_text(entity: Any) -> str:
    if entity.dxftype() == "MTEXT":
        try:
            return entity.plain_text()
        except Exception:
            return str(_dxf_get(entity, "text", ""))
    return str(_dxf_get(entity, "text", ""))


def _entity_is_closed(entity: Any, points: list[tuple[float, float]]) -> bool:
    entity_type = entity.dxftype()
    if entity_type == "CIRCLE":
        return True
    if entity_type == "ELLIPSE":
        start = float(_dxf_get(entity, "start_param", 0.0))
        end = float(_dxf_get(entity, "end_param", math.tau))
        return math.isclose(abs(end - start), math.tau, rel_tol=1e-7, abs_tol=1e-7)
    try:
        if bool(entity.is_closed):
            return True
    except Exception:
        pass
    return len(points) > 2 and _same_xy(points[0], points[-1])


def _closed_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ring = _dedupe_consecutive(points)
    if not _same_xy(ring[0], ring[-1]):
        ring.append(ring[0])
    return ring


def _orient_polygon_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ring = _closed_ring(points)
    if _signed_area(ring) > 0:
        ring = list(reversed(ring))
    return ring


def _signed_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        area += (x1 * y2) - (x2 * y1)
    return area / 2.0


def _xy(point: Any) -> tuple[float, float]:
    if point is None:
        raise ValueError("Missing coordinate")
    try:
        return (float(point.x), float(point.y))
    except AttributeError:
        return (float(point[0]), float(point[1]))


def _z(point: Any) -> float:
    if point is None:
        return 0.0
    try:
        return float(point.z)
    except AttributeError:
        try:
            return float(point[2])
        except Exception:
            return 0.0


def _same_xy(a: tuple[float, float], b: tuple[float, float], tolerance: float = 1e-8) -> bool:
    return math.isclose(a[0], b[0], abs_tol=tolerance) and math.isclose(a[1], b[1], abs_tol=tolerance)


def _dedupe_consecutive(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in points:
        if not deduped or not _same_xy(deduped[-1], point):
            deduped.append(point)
    return deduped


def _safe_text(value: Any, size: int) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ")[:size]


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0
