#!/usr/bin/env python3
"""OPTION A: offscreen KLayout view (klayout.lay.LayoutView), no window and no display needed.

Read-only: the GDS is only read. Layer visibility, zoom and the DRC/demo marker layer live in the in-memory
view; the only thing written is the snapshot PNG under build/agent/klayout_gui/.
Run inside the project venv: build/agent/venv/bin/python (klayout 0.30.x).
Docs: examples/hermes_klayout_gui/README.md, docs/HERMES_AGENT.md
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import view_api as va  # noqa: E402
from view_api import ViewBackend, ViewError  # noqa: E402

# Layer 1000/0 is outside the sky130 layer map, so demo/DRC markers can never collide with real geometry.
MARK_LD = (1000, 0)          # in-memory marker layer (never written to a file)
MARK_COLOR = 0xFF2020
VIEW_W, VIEW_H = 1200, 900


class OffscreenBackend(ViewBackend):
    name = "offscreen"

    def __init__(self):
        import klayout.db as db
        import klayout.lay as lay
        self.db, self.lay = db, lay
        self.lv = None
        self.design = None
        self.top = None
        self.gds = None
        self._req = None              # the bbox the last zoom asked for (um)
        self._seq = 0
        self._markers = 0
        self._mark_node_added = False

    # ------------------------------------------------------------ helpers
    def _need(self):
        if self.lv is None:
            raise ViewError("no design open; call open_design first")

    def _layout(self):
        return self.lv.active_cellview().layout()

    def _nodes(self):
        it = self.lv.begin_layers()
        while not it.at_end():
            yield it.dup(), it.current()
            it.next()

    def _present(self):
        ly = self._layout()
        return {(i.layer, i.datatype) for i in ly.layer_infos()}

    def _visible(self):
        present = self._present()
        out = []
        for _, n in self._nodes():
            ld = (n.source_layer, n.source_datatype)
            if ld == MARK_LD:
                continue
            if n.visible and ld in present:
                out.append(va.layer_label(*ld))
        return sorted(set(out), key=lambda s: tuple(int(x) for x in s.split()[0].split("/")))

    def _vis_out(self):
        v = self._visible()
        return v if len(v) <= 12 else v[:12] + ["... +%d more (%d visible in total)" % (len(v) - 12, len(v))]

    def _view_box(self):
        b = self.lv.box()
        return [round(b.left, 3), round(b.bottom, 3), round(b.right, 3), round(b.top, 3)]

    def _zoom(self, bbox):
        self._req = list(bbox)
        self.lv.resize(VIEW_W, VIEW_H)
        self.lv.zoom_box(self.db.DBox(*bbox))

    # ------------------------------------------------------------ API
    def _open_design(self, design):
        gds = va.find_gds(design)
        lv = self.lay.LayoutView()
        lv.load_layout(gds, True)
        if os.path.isfile(va.eda_tools.LYP):
            lv.load_layer_props(va.eda_tools.LYP)          # sky130A colours, if the PDK is installed
        lv.max_hier()
        self.lv, self.design, self.gds = lv, design, gds
        self._markers, self._mark_node_added = 0, False
        ly = self._layout()
        self.top = ly.top_cell()
        full = self._full_bbox()
        self._zoom(full)
        return {"design": design, "top_cell": self.top.name, "gds": va.rel(gds), "bbox_um": [round(v, 3) for v in full],
                "n_layers": len(self._present()), "view_bbox_um": self._view_box()}

    def _full_bbox(self):
        b = self.top.dbbox()
        return [b.left, b.bottom, b.right, b.top]

    def _find_cell_bbox(self, name):
        """Resolve a cell name or an instance name (e.g. mprj) to a bbox in top-cell coordinates (um)."""
        ly, top = self._layout(), self.top
        if name == top.name:
            return self._full_bbox(), "top cell"
        for inst in top.each_inst():                      # 1) instance name, a direct child of the top cell
            if name in [str(v) for v in inst.properties().values()]:
                b = inst.dbbox()
                return [b.left, b.bottom, b.right, b.top], "instance %s of cell %s" % (name, inst.cell.name)
        cands = [c for c in ly.each_cell() if c.name == name]          # 2) exact cell name
        if not cands:
            cands = [c for c in ly.each_cell() if name.lower() in c.name.lower()]   # 3) unique substring
            if len(cands) != 1:
                names = sorted(c.name for c in cands)[:8]
                raise ViewError("no cell or instance %r%s" % (name, ("; ambiguous: " + ", ".join(names)) if names else ""))
        cell = cands[0]
        ri = self.db.RecursiveInstanceIterator(ly, top)       # first placement of that cell, in top coordinates
        while not ri.at_end():
            if ri.inst_cell().cell_index() == cell.cell_index():
                b = ri.dtrans() * ri.inst_cell().dbbox()
                return [b.left, b.bottom, b.right, b.top], "first placement of cell %s" % cell.name
            ri.next()
        b = cell.dbbox()
        return [b.left, b.bottom, b.right, b.top], "cell %s (not placed under the top cell; own coordinates)" % cell.name

    def _zoom_to(self, target):
        self._need()
        if not isinstance(target, dict):
            raise ViewError("target must be an object")
        extra = ""
        if target.get("full"):
            bb = self._full_bbox()
        elif "bbox" in target:
            bb = va.check_bbox(target["bbox"])
        elif "cell" in target:
            if not isinstance(target["cell"], str):
                raise ViewError("cell must be a string")
            bb, extra = self._find_cell_bbox(target["cell"])
            pad = 0.05 * max(bb[2] - bb[0], bb[3] - bb[1])
            bb = [bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad]
        else:
            raise ViewError("target needs one of cell, bbox, full")
        self._zoom(bb)
        r = {"view_bbox_um": self._view_box()}
        if extra:
            r["resolved"] = extra
        return r

    def _show_layers(self, layers, only=True):
        self._need()
        want = va.parse_layers(layers)
        present = self._present()
        missing = [va.layer_label(*ld) for ld in want if ld not in present]
        hit = [ld for ld in want if ld in present]
        if not hit:
            raise ViewError("none of the requested layers exist in this layout: %s" % ", ".join(missing))
        for it, n in self._nodes():
            ld = (n.source_layer, n.source_datatype)
            if ld == MARK_LD:
                continue
            if ld in hit:
                n.visible = True
            elif only:
                n.visible = False
            else:
                continue
            self.lv.set_layer_properties(it, n)
        r = {"visible": self._vis_out()}
        if missing:
            r["not_in_layout"] = missing
        return r

    def _ensure_mark_layer(self):
        ly = self._layout()
        ly.layer(self.db.LayerInfo(MARK_LD[0], MARK_LD[1], "DRC markers"))
        if not self._mark_node_added:
            node = self.lay.LayerPropertiesNode()
            node.name = "DRC markers (in-memory)"
            node.source_layer, node.source_datatype = MARK_LD
            node.fill_color = node.frame_color = MARK_COLOR
            node.dither_pattern = 0
            node.width = 2
            self.lv.insert_layer(self.lv.end_layers(), node)
            self._mark_node_added = True
        return ly.layer(self.db.LayerInfo(MARK_LD[0], MARK_LD[1]))

    def _highlight_drc(self, design, max_items=200, demo_markers=None):
        self._need()
        if design != self.design:
            raise ViewError("design %r is not the open design (%s); open_design first" % (design, self.design))
        max_items = max(1, min(int(max_items), 1000))
        boxes, info = [], {}
        if demo_markers:
            boxes = va.demo_boxes(self._req) if demo_markers is True else va.check_markers(demo_markers)
            info = {"source": "demo markers (NOT real DRC errors)", "demo": True}
        else:
            rep = va.find_drc_report(design)
            total_json = va.drc_summary_json(design)
            if rep is None:
                return {"n_markers": 0, "note": "no KLayout DRC report database found for %s; committed per-rule counts "
                        "(output/reports/drc_klayout.json) sum to %s" % (design, total_json)}
            import klayout.rdb as rdb
            db_ = rdb.ReportDatabase("drc")
            db_.load(rep)
            n_items = db_.num_items()
            for item in db_.each_item():
                for v in item.each_value():
                    b = v.box() if v.is_box() else v.polygon().bbox() if v.is_polygon() else None
                    if b is not None and len(boxes) < max_items:
                        boxes.append([b.left, b.bottom, b.right, b.top])
            info = {"source": va.rel(rep), "rdb_items": n_items}
            if n_items == 0:
                info["note"] = "KLayout DRC report has 0 violations: the design is DRC-clean (matches %s)" % (
                    "output/reports/drc_klayout.json total %s" % total_json if total_json is not None else "the flow metrics")
        idx = self._ensure_mark_layer()
        shapes = self.top.shapes(idx)
        shapes.clear()
        for b in boxes[:max_items]:
            shapes.insert(self.db.DBox(*b))
        self._markers = len(boxes[:max_items])
        self.lv.update_content()
        info["n_markers"] = self._markers
        if boxes:
            info["first_markers_um"] = [[round(v, 3) for v in b] for b in boxes[:3]]
        return info

    def _snapshot(self, path=None, width=VIEW_W, height=VIEW_H):
        self._need()
        width, height = va.clamp_px(width, VIEW_W), va.clamp_px(height, VIEW_H)
        self._seq += 1
        out = va.check_png_path(path, self.design, self._seq)
        self.lv.resize(width, height)
        self.lv.zoom_box(self.db.DBox(*self._req))
        self.lv.save_image_with_options(out, width, height, 0, 0, 0, self.db.DBox(), False)
        self.lv.resize(VIEW_W, VIEW_H)
        if not os.path.isfile(out) or os.path.getsize(out) == 0:
            raise ViewError("render produced an empty file")
        return {"png": va.rel(out), "bytes": os.path.getsize(out), "size_px": [width, height],
                "view_bbox_um": self._view_box(), "visible_layers": self._vis_out(), "markers": self._markers}

    def _state(self):
        if self.lv is None:
            return {"design": None, "view_bbox_um": None, "visible_layers": [], "note": "no design open"}
        return {"design": self.design, "top_cell": self.top.name, "view_bbox_um": self._view_box(),
                "visible_layers": self._vis_out(), "markers": self._markers}
