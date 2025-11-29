#!/usr/bin/env python3

# Copyright Callan Bryant <callan.bryant@gmail.com>

import sys
from pathlib import Path
import numpy
import stl
import rectpack
import math
from subprocess import run
from os.path import join
from os.path import dirname
from os.path import realpath
from os import listdir
from tempfile import TemporaryDirectory

# 3d printer, X1 carbon, biggest possible rectangle
# found by resizing and positioning cube
#BED_SIZE = (236, 255)

# prusa i3 mk3
#BED_SIZE = (200, 200)

# Bambu Labs A1 Mini
BED_SIZE = (180, 180)

# half of this is applied as offset to each part, so adjacent parts are PART_GAP apart
PART_GAP = 1

SCRIPT_DIR = dirname(realpath(__file__))

cut_template = """
$fn=128;
use <{dovetail_path}>;
difference() {{
    import("{filename}");
    translate([{x_center}, {y}, 0]) teeth_cut_3d({length}, {height});
}}
"""


# can occur at dovetail boundary
def part_is_artefact(part):
    return part.xspan < part.zspan and part.yspan < part.zspan and part.z.min() > 0.01


def arrange_to_beds(parts, bed_size):
    packer = rectpack.newPacker(rotation=True)
    # allow unlimited beds
    packer.add_bin(*bed_size, float("inf"))

    for part in parts:
        assert part.fits(
            bed_size
        ), f"{part.name} {float(part.bbox[0])}x{float(part.bbox[1])} bigger than stock size {bed_size}"

        part.reset_origin()
        packer.add_rect(*part.bbox, part.name)

    packer.pack()

    beds = list()

    # move parts
    for bed_id, bed_bin in enumerate(packer):
        bin_parts = list()

        for rect in bed_bin:
            part = {part.name: part for part in parts}[rect.rid]
            rotated = rect.width != part.bbox[0]
            part.position_bbox(rect.x, rect.y, rotated)
            bin_parts.append(part)

        bed = Part(numpy.concatenate([part.data for part in bin_parts]))
        bed.name = f"bed-{bed_id+1}"
        bed.reset_origin()
        beds.append(bed)

    return beds


class Part(stl.mesh.Mesh):
    def reset_origin(self, cnc_origin=False):
        self.points[:, (0, 3, 6)] += -self.x.min()
        self.points[:, (1, 4, 7)] += -self.y.min()
        self.points[:, (2, 5, 8)] += -self.z.min()

    @classmethod
    def from_file(cls, filename):
        part = super().from_file(filename)
        part.name = filename  # could be file name/path but doesn't have to be. Useful for debugging etc
        return part

    @property
    def xspan(self):
        return self.x.max() - self.x.min()

    @property
    def yspan(self):
        return self.y.max() - self.y.min()

    @property
    def zspan(self):
        return self.z.max() - self.z.min()

    @property
    def bbox(self):
        """get 2d bounding box with margin applied (half around perimeter) as rect"""
        # see rectpack docs: decimal is required to prevent float collisions
        return (
            rectpack.float2dec(self.xspan + PART_GAP, 1),
            rectpack.float2dec(self.yspan + PART_GAP, 1),
        )

    def rotatez(self, angle):
        self.rotate([0.0, 0.0, 0.5], math.radians(angle))

    def position_bbox(self, x, y, rotate):
        """move Part in 2d by bounding box (accounting for margin), restoring origin"""
        # it references bbox because it assumes a PART_GAP margin, like the bbox property
        if rotate:
            self.rotatez(90)

        self.reset_origin()
        self.points[:, (0, 3, 6)] += float(x) + PART_GAP / 2
        self.points[:, (1, 4, 7)] += float(y) + PART_GAP / 2

    def fits(self, bed_size):
        # rotate to aspect ratio of bed to perhaps pack better
        # (and also allow us to check if it will fit!)
        if (self.bbox[0] > self.bbox[1] and bed_size[0] < bed_size[1]) or (
            self.bbox[0] < self.bbox[1] and bed_size[0] > bed_size[1]
        ):
            self.rotatez(90)

        return self.bbox[0] <= bed_size[0] and self.bbox[1] <= bed_size[1]

    def dovetail_at_y(self, y):
        with TemporaryDirectory(dir=SCRIPT_DIR) as tmpdir:
            scad_file = join(tmpdir, "in.scad")
            input_stl = join(tmpdir, "in.stl")
            output_stl = join(tmpdir, "out.stl")

            self.save(input_stl, mode=stl.Mode.BINARY)

            with open(scad_file, "w") as f:
                f.write(
                    cut_template.format(
                        dovetail_path=join(SCRIPT_DIR, "lib/dovetail.scad"),
                        filename=input_stl,
                        x_center=self.xspan / 2,
                        y=y,
                        length=self.xspan,
                        height=self.zspan
                    )
                )

            # https://fosstodon.org/@OpenSCAD/113256867413539398
            run(["flatpak", "run", "org.openscad.OpenSCAD//beta", "--backend=manifold", scad_file, "-o", output_stl], check=True, cwd=SCRIPT_DIR)

            return Part.from_file(output_stl)

    def separate_into_parts(self):
        """Split disconnected parts into individual Parts, leaving nested islands."""
        with TemporaryDirectory(dir=SCRIPT_DIR) as tmpdir:
            tmpfile = join(tmpdir, "part.stl")
            self.save(tmpfile, mode=stl.Mode.BINARY)
            run(["flatpak", "run", "--command=/app/bin/prusa-slicer", "com.prusa3d.PrusaSlicer", "--split", tmpfile], check=True, cwd=tmpdir)

            return [
                Part.from_file(join(tmpdir, f))
                for f in listdir(tmpdir)
                if f.startswith("part.stl_")
            ]

    def make_jigsaw(self, bed_size):
        """Subdivide in X and Y with dovetail joints to allow printing on the given bed."""
        self.reset_origin()

        assert self.xspan > bed_size[0] or self.yspan > bed_size[1], "Part is smaller than bed"

        # calc min number of pieces
        # TODO account for real tooth size, this is copied
        tooth_size = 5
        y_pieces = math.ceil((self.yspan + tooth_size) / bed_size[1])
        x_pieces = math.ceil((self.xspan + tooth_size) / bed_size[0])

        # calc actual piece sizes
        y_size = self.yspan / y_pieces
        x_size = self.xspan / x_pieces

        # if no cuts are made (which should be impossible given assertions)
        part = self

        for i in range(1, y_pieces):
            part = part.dovetail_at_y(y_size * i)
            part.reset_origin()   # just to be sure, should not change though

        part.rotatez(90)
        part.reset_origin()

        for i in range(1, x_pieces):
            part = part.dovetail_at_y(x_size * i)
            part.reset_origin()   # just to be sure, should not change though

        # back to original orientation
        part.rotatez(270)
        part.reset_origin()

        # Return single part with all grooves cut in
        part.name = "jigsaw_assembled"
        return [part]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: %s <stl file...> <output_dir/>" % sys.argv[0])
        sys.exit(1)

    output_dir = Path(sys.argv[-1])
    output_dir.mkdir(parents=True, exist_ok=True)


    original_parts = [Part.from_file(filepath) for filepath in sorted(sys.argv[1:-1])]
    parts = []

    for part in original_parts:
        if part.fits(BED_SIZE):
            parts.append(part)
        else:
            print(f"Part {part.name} does not fit, making jigsaw pieces...")
            parts += part.make_jigsaw(BED_SIZE)


    # Save jigsaw parts directly without bed arrangement
    for part in parts:
        part.save(output_dir / f"{part.name}.stl", mode=stl.Mode.BINARY)

    print(f"Saved {len(parts)} part(s) to {output_dir}/")
