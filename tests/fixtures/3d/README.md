# Licensed real scan fixture

`abalone-scan.geometry.ply` and `abalone-scan.geometry.glb` are geometry-only
derivatives of the **IridescenceAbalone** photogrammetry scan from the official
[Khronos glTF Sample Assets repository](https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/IridescenceAbalone).
The upstream [README](https://github.com/KhronosGroup/glTF-Sample-Assets/blob/main/Models/IridescenceAbalone/README.md)
identifies the model explicitly as a photogrammetry scan of an abalone shell.
This is a real captured object, **not an apartment scan or LiDAR validation**.

Attribution: © 2015 Abby Crawford (everything); © 2023 Eric Chadwick (materials).
Licensed under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)
([legal code](https://creativecommons.org/licenses/by/4.0/legalcode)).
Source: [official GLB](https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/IridescenceAbalone/glTF-Binary/IridescenceAbalone.glb).

Changes by the redcn contributors: removed materials, images, normals, UVs and
source metadata; applied the original static scene rotation; retained every
vertex and triangle without resampling. Exported only geometry to PLY ASCII 1.0
and an uncompressed static GLB 2.0. The original source is not distributed here.
No source unit is inferred by the viewer. Measurements require the operator to
declare a unit, and are not engineering measurements.

`abalone-scan.provenance.json` records exact source and derivative SHA-256 values,
byte sizes, vertex/triangle counts and changes. Reproduce from the exact public
source (download it separately) with:

```sh
node tests/fixtures/3d/derive-public-scan.mjs /path/to/IridescenceAbalone.glb
```

The converter never changes the input file and rejects a source SHA-256 mismatch.
It performs no downloads. Synthetic triangles used by unit tests are explicitly
test geometry and are never described as a captured scan.
