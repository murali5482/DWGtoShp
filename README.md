# DWG to Shapefile

Standalone Windows tool for converting CAD drawings (`.dxf` and `.dwg`) into ESRI shapefiles.

The converter writes separate shapefiles by geometry type because a shapefile can contain only one geometry type:

- `<drawing>_points.shp`
- `<drawing>_lines.shp`
- `<drawing>_polygons.shp`

## Build the executable

Run this in PowerShell from the project folder:

```powershell
.\build_exe.ps1
```

The build creates:

- `dist\DWGtoShp.exe` - desktop app with file pickers
- `dist\dwg-to-shp-cli.exe` - command-line converter

## Command-line usage

```powershell
dist\dwg-to-shp-cli.exe "C:\path\site-plan.dxf" -o "C:\path\output"
```

Optional projection sidecar:

```powershell
dist\dwg-to-shp-cli.exe "C:\path\site-plan.dxf" -o "C:\path\output" --prj "C:\path\source.prj"
```

You can download matching `.prj` projection files from [Spatial Reference](https://spatialreference.org/ref/).

Keep closed polylines as line features:

```powershell
dist\dwg-to-shp-cli.exe "C:\path\site-plan.dxf" --no-polygons
```

## DWG support

DXF files are read directly by the executable.

DWG is proprietary, so the executable uses one of these installed converters when available:

- GDAL `ogr2ogr` in `PATH`
- ODA File Converter discoverable by `ezdxf`

If neither is installed, save/export the drawing as DXF and run the tool on the DXF file.

## Output attributes

Each feature includes:

- `layer`
- `cad_type`
- `handle`
- `color`
- `linetype`
- `text`
- `block`
- `elev`
- `closed`
