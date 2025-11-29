# 3D Jigsaw Generator

Split large 3D models into printable parts with dovetail connectors.

Based on [Cal Bryant's approach](https://calbryant.uk/blog/3d-printing-giant-things-with-a-python-jigsaw-generator/), with key differences:
- Outputs single assembled STL with grooves (no splitting step)
- Uses flatpak for OpenSCAD with manifold backend
- Fixed tooth positioning to cover entire cut lines
- Configured for Bambu Labs A1 Mini (180x180mm)

## Usage

```bash
# Install dependencies
flatpak install flathub-beta org.openscad.OpenSCAD//beta
uv sync

# Generate jigsaw pieces
just run input.stl

# In your slicer (Bambu Studio, PrusaSlicer, etc.):
# 1. Import output/jigsaw_assembled.stl
# 2. Right-click → "Split to objects"
# 3. Print each piece separately
```

## Configuration

Edit `BED_SIZE` in `turbojigsaw.py` to match your printer.
