from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .converter import CadConversionError, ConversionOptions, convert_cad_to_shapefiles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dwg-to-shp",
        description="Convert a .dwg or .dxf CAD file into ESRI shapefiles.",
    )
    parser.add_argument("input", nargs="?", help="Path to a .dwg or .dxf file.")
    parser.add_argument(
        "-o",
        "--output",
        help="Output folder. Defaults to a folder next to the input file.",
    )
    parser.add_argument(
        "--prj",
        help="Optional .prj file to copy beside each output shapefile.",
    )
    parser.add_argument(
        "--no-polygons",
        action="store_true",
        help="Keep closed CAD curves as line features instead of polygon features.",
    )
    parser.add_argument(
        "--no-ogr2ogr",
        action="store_true",
        help="Skip the GDAL ogr2ogr engine even if it is available.",
    )
    parser.add_argument(
        "--curve-segments",
        type=int,
        default=72,
        help="Segments used when approximating circles and arcs without ezdxf path support.",
    )
    parser.add_argument(
        "--curve-tolerance",
        type=float,
        default=0.25,
        help="Flattening tolerance for curved CAD entities.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the desktop interface.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui or not args.input:
        from .gui import main as gui_main

        return gui_main()

    options = ConversionOptions(
        closed_polylines_as_polygons=not args.no_polygons,
        curve_segments=max(8, args.curve_segments),
        curve_tolerance=max(0.001, args.curve_tolerance),
        prefer_ogr2ogr=not args.no_ogr2ogr,
        prj_path=Path(args.prj) if args.prj else None,
    )

    try:
        result = convert_cad_to_shapefiles(args.input, args.output, options)
    except CadConversionError as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 2

    print(f"Converted with {result.engine}")
    print(f"Output folder: {result.output_dir}")
    for shapefile_path in result.shapefiles:
        print(f"  {shapefile_path}")
    if result.skipped_entities:
        print(f"Skipped unsupported/problem entities: {result.skipped_entities}")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
