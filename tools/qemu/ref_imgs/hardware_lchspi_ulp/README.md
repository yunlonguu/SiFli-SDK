# lchspi-ulp hardware captures

96 EPIC benchmark frames captured from sf32lb52-lchspi-ulp_hcpu on 2026-10-19.
390x450 RGB565 draw-buffer exports, not physical panel photographs.
Each raw UART frame passed byte-count and FNV-1a verification; see metadata.json.
Scene names are in manifest.json; capture firmware/QEMU hashes are in provenance.json.

These are hardware observations, not a claim that every scene is visually correct.
The local patched QEMU matches 78/96 at RGB565 precision. Known differences:
034-035 (color key), 048-063 (rotation and scaling).
CI intentionally fails on differences while uploading the full three-column report.
