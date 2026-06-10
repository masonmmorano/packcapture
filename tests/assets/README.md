# Local test fixtures (not committed)

Real-footage crops used by recognition regression tests live here. The image
files are **git-ignored on purpose** — they're frames from third-party
pack-opening videos, so we keep them out of the public repo for provenance/IP
reasons. Tests that use them skip automatically when the asset is absent (e.g.
on a fresh clone / in CI), so the suite stays green without them.

## `murkrow_57.png`

A 264×316 crop of a physical **Murkrow #57** (Phantasmal Flames, `me2`) held
in-hand, used by `tests/test_real_footage.py`. The set-locked ORB matcher
recognizes it at ~48 inliers (runner-up ~6) and the confidence gate accepts it —
a real-world regression check beyond the synthetic-card tests.

### How to regenerate

Source: a 10s 480p clip (`scratch/footage/`, also git-ignored) from the
"I opened 216 packs of Phantasmal Flames" video by Full Heal, frame `f00240`.

```powershell
.\.venv\Scripts\python.exe -c "import cv2; img=cv2.imread('scratch/footage/frames/f00240.png'); h,w=img.shape[:2]; x,y,bw,bh=int(w*0.13),int(h*0.14),int(w*0.31),int(h*0.66); cv2.imwrite('tests/assets/murkrow_57.png', img[y:y+bh, x:x+bw])"
```

Any clear, mostly-isolated photo of `me2` Murkrow #57 dropped in at this path
works just as well (a self-shot phone photo is the cleanest input).
