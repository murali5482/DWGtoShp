from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .converter import CadConversionError, ConversionOptions, convert_cad_to_shapefiles


class ConverterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DWG to Shapefile")
        self.minsize(680, 420)
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.prj_var = tk.StringVar()
        self.polygons_var = tk.BooleanVar(value=True)
        self.ogr_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.after(100, self._poll_worker)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        root = ttk.Frame(self, padding=16)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)

        ttk.Label(root, text="CAD file").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(root, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(root, text="Browse", command=self._browse_input).grid(row=0, column=2, padx=(8, 0), pady=6)

        ttk.Label(root, text="Output folder").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(root, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(root, text="Browse", command=self._browse_output).grid(row=1, column=2, padx=(8, 0), pady=6)

        ttk.Label(root, text="Projection .prj").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(root, textvariable=self.prj_var).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Button(root, text="Browse", command=self._browse_prj).grid(row=2, column=2, padx=(8, 0), pady=6)

        options = ttk.Frame(root)
        options.grid(row=3, column=1, sticky="w", pady=(8, 6))
        ttk.Checkbutton(
            options,
            text="Closed CAD curves become polygons",
            variable=self.polygons_var,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            options,
            text="Use ogr2ogr when available",
            variable=self.ogr_var,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        actions = ttk.Frame(root)
        actions.grid(row=4, column=1, sticky="w", pady=(12, 8))
        self.convert_button = ttk.Button(actions, text="Convert", command=self._start_conversion)
        self.convert_button.grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Open Output", command=self._open_output).grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(root, textvariable=self.status_var).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 4))

        self.log = tk.Text(root, height=10, wrap="word", state="disabled")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew")
        scrollbar = ttk.Scrollbar(root, command=self.log.yview)
        scrollbar.grid(row=6, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

    def _browse_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select CAD file",
            filetypes=[("CAD files", "*.dwg *.dxf"), ("DWG", "*.dwg"), ("DXF", "*.dxf"), ("All files", "*.*")],
        )
        if not selected:
            return
        self.input_var.set(selected)
        if not self.output_var.get().strip():
            self.output_var.set(str(Path(selected).with_suffix("")))

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(title="Select output folder")
        if selected:
            self.output_var.set(selected)

    def _browse_prj(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select projection file",
            filetypes=[("Projection files", "*.prj"), ("All files", "*.*")],
        )
        if selected:
            self.prj_var.set(selected)

    def _start_conversion(self) -> None:
        input_path = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        prj_path = self.prj_var.get().strip()
        if not input_path:
            messagebox.showerror("Missing CAD file", "Select a DWG or DXF file first.")
            return
        if not output_dir:
            output_dir = str(Path(input_path).with_suffix(""))
            self.output_var.set(output_dir)

        options = ConversionOptions(
            closed_polylines_as_polygons=self.polygons_var.get(),
            prefer_ogr2ogr=self.ogr_var.get(),
            prj_path=Path(prj_path) if prj_path else None,
        )

        self.convert_button.configure(state="disabled")
        self.status_var.set("Converting...")
        self._append_log(f"Input: {input_path}\nOutput: {output_dir}\n")
        worker = threading.Thread(
            target=self._convert_worker,
            args=(input_path, output_dir, options),
            daemon=True,
        )
        worker.start()

    def _convert_worker(self, input_path: str, output_dir: str, options: ConversionOptions) -> None:
        try:
            result = convert_cad_to_shapefiles(input_path, output_dir, options)
            self._messages.put(("success", result))
        except CadConversionError as exc:
            self._messages.put(("error", str(exc)))
        except Exception as exc:
            self._messages.put(("error", f"Unexpected error: {exc}"))

    def _poll_worker(self) -> None:
        try:
            kind, payload = self._messages.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_worker)
            return

        self.convert_button.configure(state="normal")
        if kind == "success":
            result = payload
            self.status_var.set("Conversion complete")
            self._append_log(f"Engine: {result.engine}\n")
            self._append_log("Created shapefiles:\n")
            for path in result.shapefiles:
                self._append_log(f"  {path}\n")
            if result.skipped_entities:
                self._append_log(f"Skipped unsupported/problem entities: {result.skipped_entities}\n")
            for warning in result.warnings:
                self._append_log(f"Warning: {warning}\n")
            messagebox.showinfo("Conversion complete", f"Created {len(result.shapefiles)} shapefile(s).")
        else:
            self.status_var.set("Conversion failed")
            self._append_log(f"Error: {payload}\n")
            messagebox.showerror("Conversion failed", str(payload))

        self.after(100, self._poll_worker)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_output(self) -> None:
        output_dir = self.output_var.get().strip()
        if not output_dir:
            return
        path = Path(output_dir)
        if not path.exists():
            messagebox.showerror("Folder not found", f"Output folder does not exist:\n{path}")
            return
        try:
            import os

            os.startfile(path)
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc))


def main() -> int:
    app = ConverterApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
